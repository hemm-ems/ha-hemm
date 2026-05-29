"""DataUpdateCoordinator for HEMM — runs optimization on schedule."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .actuator import ActuationDecision, ActuatorEngine
from .const import (
    CONF_ACTUATION_ENABLED,
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    CONF_WATCHDOG_TIMEOUT_SECONDS,
    DEFAULT_ACTUATION_ENABLED,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DEFAULT_WATCHDOG_TIMEOUT_SECONDS,
    DOMAIN,
    EVENT_CONSTRAINT_ADDED,
    EVENT_CONSTRAINT_RESOLVED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_ITERATION_COMPLETE,
    EVENT_SOLVER_SWITCHED,
)
from .identification import IdentificationResult, get_identifier
from .manifest_builder import build_all_manifests
from .time import HAClock

if TYPE_CHECKING:
    from collections.abc import Callable

    from hemm_core.constraints import ConstraintWindowManager
    from hemm_core.manifest.messages import ConstraintWindow, PlanMessage
    from hemm_core.solvers.protocol import SolverResult
    from hemm_core.time import Clock

# Check if hemm core solvers are available (they may not be during unit tests
# where custom_components/hemm shadows the core hemm package)
try:
    import hemm_core.solvers.protocol  # noqa: F401

    _HEMM_CORE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _HEMM_CORE_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=15)
SOLVER_TIMEOUT_SECONDS = UPDATE_INTERVAL.total_seconds() - 30
MAX_HISTORY = 20


def _create_constraint_manager(clock: Clock) -> ConstraintWindowManager:
    """Create a ConstraintWindowManager (deferred import)."""
    import hemm_core.constraints

    return hemm_core.constraints.ConstraintWindowManager(clock=clock)


class HemmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """HEMM DataUpdateCoordinator — runs optimization on schedule."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self._clock: Clock = clock if clock is not None else HAClock()
        self._horizon_hours: int = entry.options.get(
            CONF_HORIZON_HOURS,
            entry.data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
        )
        self._max_iterations: int = entry.options.get(
            CONF_MAX_ITERATIONS,
            entry.data.get(CONF_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS),
        )
        self._price_adapter: str = entry.options.get(
            CONF_PRICE_ADAPTER,
            entry.data.get(CONF_PRICE_ADAPTER, DEFAULT_PRICE_ADAPTER),
        )
        self._solver_backend: str = entry.options.get(
            CONF_SOLVER_BACKEND,
            entry.data.get(CONF_SOLVER_BACKEND, DEFAULT_SOLVER_BACKEND),
        )
        self._actuation_enabled: bool = entry.options.get(
            CONF_ACTUATION_ENABLED,
            entry.data.get(CONF_ACTUATION_ENABLED, DEFAULT_ACTUATION_ENABLED),
        )
        self._watchdog_timeout_seconds: int = entry.options.get(
            CONF_WATCHDOG_TIMEOUT_SECONDS,
            entry.data.get(CONF_WATCHDOG_TIMEOUT_SECONDS, DEFAULT_WATCHDOG_TIMEOUT_SECONDS),
        )

        # Solver and constraint state
        self._constraint_manager: ConstraintWindowManager | None = None
        self._previous_plans: list[PlanMessage] = []
        self._iteration_count: int = 0
        self._last_result: SolverResult | None = None
        self._lambda_history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._dry_run_log: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._id_results: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._actuator = ActuatorEngine(hass, clock=self._clock)
        self._last_successful_update = self._clock.now()
        self._watchdog_unsub: Callable[[], None] | None = None

        # Manual price override (set via hemm.set_price_curve service)
        self._manual_prices: list[float] | None = None
        self._manual_price_resolution: int = 15
        self._currently_solving: bool = False

    @property
    def horizon_hours(self) -> int:
        """Return optimization horizon in hours."""
        return self._horizon_hours

    @property
    def solver_backend(self) -> str:
        """Return active solver backend name."""
        return self._solver_backend

    @property
    def price_adapter(self) -> str:
        """Return active price adapter name."""
        return self._price_adapter

    @property
    def actuation_enabled(self) -> bool:
        """Return whether live actuation is enabled."""
        return self._actuation_enabled

    @property
    def watchdog_timeout_seconds(self) -> int:
        """Return watchdog timeout in seconds."""
        return self._watchdog_timeout_seconds

    @property
    def actuator(self) -> ActuatorEngine:
        """Return the actuator engine."""
        return self._actuator

    @property
    def actuation_audit_log(self) -> list[dict[str, Any]]:
        """Return anonymized actuation audit entries."""
        return self._actuator.audit_log

    @property
    def constraint_manager(self) -> ConstraintWindowManager:
        """Return the constraint window manager."""
        if self._constraint_manager is None:
            self._constraint_manager = _create_constraint_manager(self._clock)
        return self._constraint_manager

    @property
    def clock(self) -> Clock:
        """Return the clock used for all time reads in this coordinator."""
        return self._clock

    @property
    def last_result(self) -> SolverResult | None:
        """Return the last solver result."""
        return self._last_result

    @property
    def dry_run_log(self) -> list[dict[str, Any]]:
        """Return the dry-run audit log."""
        return list(self._dry_run_log)

    @property
    def id_results(self) -> list[dict[str, Any]]:
        """Return identification results history."""
        return list(self._id_results)

    def start_watchdog(self) -> None:
        """Start periodic watchdog checks."""
        if self._watchdog_unsub is not None:
            return
        interval = timedelta(seconds=max(30, min(self._watchdog_timeout_seconds, 300)))
        self._watchdog_unsub = async_track_time_interval(self.hass, self._async_watchdog_tick, interval)

    def stop_watchdog(self) -> None:
        """Stop periodic watchdog checks."""
        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None

    def _get_solver(self) -> Any:
        """Create a solver instance for the active backend."""
        if self._solver_backend == "distributed":
            from hemm_core.solvers.distributed import DistributedSolver

            return DistributedSolver(max_iterations=self._max_iterations, clock=self._clock)

        from hemm_core.solvers.milp_central import MILPCentralSolver

        return MILPCentralSolver(clock=self._clock)

    def _get_price_forecast(self) -> list[tuple[datetime, float]]:
        """Fetch price forecast from manual override or configured adapter."""
        now = self._clock.now()

        # Prefer manual prices set via hemm.set_price_curve
        if self._manual_prices is not None:
            res_min = self._manual_price_resolution
            _LOGGER.info("Using manual price curve (%d slots, %d min resolution)", len(self._manual_prices), res_min)
            return [(now + timedelta(minutes=i * res_min), p) for i, p in enumerate(self._manual_prices)]

        try:
            from hemm_core.adapters.registry import get_registry

            registry = get_registry()
            adapter = registry.get(self._price_adapter)
            points = adapter.fetch(horizon_hours=self._horizon_hours)
            return [(p.timestamp, p.value) for p in points]
        except Exception:
            _LOGGER.warning("Price adapter '%s' failed, using flat price", self._price_adapter)
            return [(now + timedelta(minutes=i * 15), 0.30) for i in range(self._horizon_hours * 4)]

    def switch_solver(self, backend: str) -> None:
        """Switch the active solver backend at runtime."""
        old = self._solver_backend
        self._solver_backend = backend
        _LOGGER.info("Solver switched: %s -> %s", old, backend)
        self.hass.bus.async_fire(
            EVENT_SOLVER_SWITCHED,
            {"old_backend": old, "new_backend": backend},
        )

    def add_constraint_window(self, window: ConstraintWindow) -> None:
        """Add a constraint window and fire event."""
        self.constraint_manager.add(window)
        self.hass.bus.async_fire(
            EVENT_CONSTRAINT_ADDED,
            {"window_id": window.window_id, "device_id": window.device_id},
        )

    def remove_constraint(self, window_id: str) -> Any:
        """Remove a constraint window and fire event if found."""
        removed = self.constraint_manager.remove(window_id)
        if removed:
            self.hass.bus.async_fire(
                EVENT_CONSTRAINT_RESOLVED,
                {"window_id": window_id, "device_id": removed.device_id},
            )
        return removed

    def bump_priority(self, window_id: str, new_penalty: float) -> bool:
        """Update priority for a constraint window."""
        return self.constraint_manager.bump_priority(window_id, new_penalty)

    async def async_run_solver(self, *, dry_run: bool = False, device_filter: list[str] | None = None) -> Any:
        """Run the optimization solver.

        Args:
            dry_run: If True, run the solver but don't update plans.
            device_filter: If provided, only re-optimize these device IDs.

        Returns:
            The solver result.
        """
        from hemm_core.solvers.protocol import SolverResult, SolverStatus

        if self._currently_solving:
            _LOGGER.debug("Solver already running, skipping")
            return self._last_result or SolverResult(status=SolverStatus.OPTIMAL)

        self._currently_solving = True
        try:
            return await self._do_solve(dry_run=dry_run, device_filter=device_filter)
        finally:
            self._currently_solving = False

    async def _do_solve(self, *, dry_run: bool = False, device_filter: list[str] | None = None) -> Any:
        """Internal solver execution (no re-entrancy guard)."""
        from hemm_core.solvers.protocol import SolverResult, SolverStatus

        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])
        if not devices:
            return SolverResult(status=SolverStatus.OPTIMAL)

        manifests = build_all_manifests(devices)

        # Apply device_filter: only pass matching manifests to solver
        if device_filter:
            filtered_manifests = [m for m in manifests if m.device_id in device_filter]
            unknown_ids = set(device_filter) - {m.device_id for m in manifests}
            if unknown_ids:
                _LOGGER.warning("device_filter contains unknown device IDs: %s", unknown_ids)
            if not filtered_manifests:
                _LOGGER.warning("device_filter matched no devices, skipping replan")
                return SolverResult(status=SolverStatus.OPTIMAL)
            manifests = filtered_manifests

        now = self._clock.now()

        # Expire old constraint windows
        expired = self.constraint_manager.expire_old(now)
        for wid in expired:
            self.hass.bus.async_fire(EVENT_CONSTRAINT_RESOLVED, {"window_id": wid})

        active_windows = self.constraint_manager.get_active(now)
        price_forecast = await self.hass.async_add_executor_job(self._get_price_forecast)
        solver = self._get_solver()

        result: SolverResult = await self.hass.async_add_executor_job(
            solver.solve,
            manifests,
            active_windows,
            price_forecast,
            self._horizon_hours * 60,
            15,
            self._previous_plans if self._previous_plans else None,
        )

        if dry_run:
            entry = {
                "timestamp": self._clock.now().isoformat(),
                "status": result.status.value,
                "solver": self._solver_backend,
                "objective": result.objective_value,
                "solve_time": result.solve_time_seconds,
                "plan_count": len(result.plans),
            }
            self._dry_run_log.append(entry)
            self.hass.bus.async_fire(EVENT_DRY_RUN_COMPLETED, entry)
            for decision in self._decisions_from_result(result, manifests):
                await self._actuator.async_actuate(
                    decision,
                    actuation_enabled=self._actuation_enabled,
                    dry_run=True,
                    pre_call_check=self._async_pre_call_check,
                )
            return result

        # Apply results
        self._last_result = result
        self._iteration_count += 1
        if result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            self._previous_plans = result.plans
            for decision in self._decisions_from_result(result, manifests):
                await self._actuator.async_actuate(
                    decision,
                    actuation_enabled=self._actuation_enabled,
                    pre_call_check=self._async_pre_call_check,
                )
            self._last_successful_update = self._clock.now()

        # Record lambda history
        self._lambda_history.append(
            {
                "iteration": self._iteration_count,
                "timestamp": self._clock.now().isoformat(),
                "status": result.status.value,
                "objective": result.objective_value,
                "solve_time": result.solve_time_seconds,
            }
        )

        # Fire iteration complete event
        self.hass.bus.async_fire(
            EVENT_ITERATION_COMPLETE,
            {
                "iteration": self._iteration_count,
                "status": result.status.value,
                "solver": self._solver_backend,
                "solve_time": result.solve_time_seconds,
                "plan_count": len(result.plans),
            },
        )

        return result

    def _decisions_from_result(self, result: Any, manifests: list[Any]) -> list[ActuationDecision]:
        """Map current plan slots to manifest actions by slot.mode."""
        manifest_map = {manifest.device_id: manifest for manifest in manifests}
        decisions: list[ActuationDecision] = []
        for plan in getattr(result, "plans", []) or []:
            manifest = manifest_map.get(plan.device_id)
            if manifest is None or not getattr(plan, "slots", None):
                continue
            control_class = str(getattr(manifest, "control_class", "planned"))
            if control_class.endswith("passive"):
                continue
            slot = plan.slots[0]
            mode = slot.mode
            if not mode:
                continue
            action = getattr(manifest, "actions", {}).get(mode)
            if action is None:
                continue
            decisions.append(
                ActuationDecision(
                    device_id=plan.device_id,
                    action=action,
                    safe_default=manifest.safe_default,
                    plan_mode=mode,
                )
            )
        return decisions

    async def _async_pre_call_check(self, device_id: str, action: Any) -> bool:
        """Re-check hard constraints and current state immediately before a script call."""
        try:
            for window in self.constraint_manager.get_active(self._clock.now()):
                if getattr(window, "device_id", None) != device_id:
                    continue
                requirement = getattr(window, "requirement", None)
                if requirement is not None and requirement.__class__.__name__ == "ForbiddenWindow":
                    return False
        except (ImportError, ModuleNotFoundError):
            return True
        except Exception:
            _LOGGER.exception("Pre-call constraint re-check failed for %s", device_id)
            return False

        verify = getattr(action, "verify", None)
        if verify is not None:
            state = self.hass.states.get(verify.entity)
            if state is not None and state.state in {"unavailable", "unknown"}:
                return False
        return True

    async def _async_watchdog_tick(self, _now: datetime) -> None:
        """Periodic watchdog entry point."""
        await self.async_check_watchdog()

    async def async_check_watchdog(self) -> bool:
        """Run the watchdog once. Returns True when it fired."""
        elapsed = (self._clock.now() - self._last_successful_update).total_seconds()
        if elapsed < self._watchdog_timeout_seconds:
            return False

        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])
        if not devices:
            self._last_successful_update = self._clock.now()
            return False

        manifests = build_all_manifests(devices)
        decisions = [
            ActuationDecision(
                device_id=manifest.device_id, action=manifest.safe_default, safe_default=manifest.safe_default
            )
            for manifest in manifests
        ]
        await self._actuator.async_watchdog_safe_defaults(decisions, reason="watchdog_timeout")
        self._last_successful_update = self._clock.now()
        self.async_set_updated_data(self._build_data())
        return True

    async def async_run_identification(self) -> list[IdentificationResult]:
        """Run online identification for all devices."""
        results: list[IdentificationResult] = []
        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])

        for device in devices:
            device_type = device.get("device_type", "")
            device_id = device.get("id", "")
            identifier = get_identifier(device_type)
            if identifier is None:
                continue

            # Pass empty observations for now (stubs return None)
            id_result = await self.hass.async_add_executor_job(identifier.identify, [])
            if id_result is not None:
                id_result.device_id = device_id
                results.append(id_result)
                self._id_results.append(
                    {
                        "timestamp": self._clock.now().isoformat(),
                        "device_id": device_id,
                        "device_type": device_type,
                        "updates": id_result.parameter_updates,
                        "confidence": id_result.confidence,
                        "message": id_result.message,
                    }
                )

        return results

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data — schedules solver as a background task and returns immediately.

        Called by DataUpdateCoordinator on the 15-min schedule and on
        async_request_refresh().  The solver runs asynchronously so that
        async_config_entry_first_refresh() does not block setup.  Overlapping
        solver runs are prevented by the _currently_solving re-entrancy guard.
        """
        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])

        # Schedule solver as a non-blocking background task
        if devices and _HEMM_CORE_AVAILABLE and not self._currently_solving:
            self.hass.async_create_task(self._run_solver_background())

        return self._build_data()

    async def _run_solver_background(self) -> None:
        """Run solver in background and notify listeners when done."""
        try:
            await asyncio.wait_for(
                self.async_run_solver(dry_run=False),
                timeout=SOLVER_TIMEOUT_SECONDS,
            )
            self.async_set_updated_data(self._build_data())
            self._last_successful_update = self._clock.now()
        except TimeoutError:
            _LOGGER.error("Solver timed out after %s s", SOLVER_TIMEOUT_SECONDS)
        except Exception:
            _LOGGER.exception("Solver run failed")

    def _build_data(self) -> dict[str, Any]:
        """Build the coordinator data dict from current cached state."""
        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])
        device_plans: dict[str, dict[str, Any]] = {}
        last_status = "idle"
        last_solve_time = 0.0
        last_plans: list[Any] = []

        if self._last_result is not None and _HEMM_CORE_AVAILABLE:
            try:
                from hemm_core.solvers.protocol import SolverStatus

                result = self._last_result
                last_status = result.status.value
                last_solve_time = result.solve_time_seconds
                last_plans = [p.model_dump() for p in result.plans] if result.plans else []

                if result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
                    plan_map: dict[str, PlanMessage] = {p.device_id: p for p in result.plans}
                    for device in devices:
                        device_id = device.get("id", "unknown")
                        plan = plan_map.get(device_id)
                        if plan and plan.slots:
                            slot = plan.slots[0]
                            device_plans[device_id] = {
                                "power_kw": slot.power_kw,
                                "confidence_pct": 95.0 if result.status == SolverStatus.OPTIMAL else 70.0,
                                "mode": slot.mode or "active",
                                "reason": slot.reason.value if slot.reason else "idle",
                            }
                        else:
                            device_plans[device_id] = {
                                "power_kw": 0.0,
                                "confidence_pct": 0.0,
                                "mode": "idle",
                                "reason": "idle",
                            }
                else:
                    for device in devices:
                        device_id = device.get("id", "unknown")
                        device_plans[device_id] = {
                            "power_kw": 0.0,
                            "confidence_pct": 0.0,
                            "mode": "error" if result.status == SolverStatus.ERROR else "idle",
                            "reason": "safety_default" if result.status == SolverStatus.ERROR else "idle",
                        }
            except Exception:
                _LOGGER.debug("Could not read cached solver result", exc_info=True)
                last_status = "error"

        # Fill stubs for any devices without plans
        for device in devices:
            device_id = device.get("id", "unknown")
            if device_id not in device_plans:
                device_plans[device_id] = {
                    "power_kw": 0.0,
                    "confidence_pct": 0.0,
                    "mode": "idle",
                    "reason": "idle",
                }

        return {
            "horizon_hours": self._horizon_hours,
            "max_iterations": self._max_iterations,
            "price_adapter": self._price_adapter,
            "solver_backend": self._solver_backend,
            "last_plans": last_plans,
            "iteration_count": self._iteration_count,
            "device_plans": device_plans,
            "last_status": last_status,
            "last_solve_time": last_solve_time,
            "actuation_enabled": self._actuation_enabled,
            "actuation_audit_count": len(self._actuator.audit_log),
        }
