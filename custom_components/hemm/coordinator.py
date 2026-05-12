"""DataUpdateCoordinator for HEMM — runs optimization on schedule."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
    EVENT_CONSTRAINT_ADDED,
    EVENT_CONSTRAINT_RESOLVED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_ITERATION_COMPLETE,
    EVENT_SOLVER_SWITCHED,
)
from .identification import IdentificationResult, get_identifier
from .manifest_builder import build_all_manifests

if TYPE_CHECKING:
    from hemm.constraints import ConstraintWindowManager
    from hemm.manifest.messages import ConstraintWindow, PlanMessage
    from hemm.solvers.protocol import SolverResult

# Check if hemm core solvers are available (they may not be during unit tests
# where custom_components/hemm shadows the core hemm package)
try:
    import hemm.solvers.protocol  # noqa: F401

    _HEMM_CORE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _HEMM_CORE_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=15)
MAX_HISTORY = 20


def _create_constraint_manager() -> ConstraintWindowManager:
    """Create a ConstraintWindowManager (deferred import)."""
    import hemm.constraints

    return hemm.constraints.ConstraintWindowManager()


class HemmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """HEMM DataUpdateCoordinator — runs optimization on schedule."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
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

        # Solver and constraint state
        self._constraint_manager: ConstraintWindowManager | None = None
        self._previous_plans: list[PlanMessage] = []
        self._iteration_count: int = 0
        self._last_result: SolverResult | None = None
        self._lambda_history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._dry_run_log: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._id_results: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)

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
    def constraint_manager(self) -> ConstraintWindowManager:
        """Return the constraint window manager."""
        if self._constraint_manager is None:
            self._constraint_manager = _create_constraint_manager()
        return self._constraint_manager

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

    def _get_solver(self) -> Any:
        """Create a solver instance for the active backend."""
        if self._solver_backend == "distributed":
            from hemm.solvers.distributed import DistributedSolver

            return DistributedSolver(max_iterations=self._max_iterations)

        from hemm.solvers.milp_central import MILPCentralSolver

        return MILPCentralSolver()

    def _get_price_forecast(self) -> list[tuple[datetime, float]]:
        """Fetch price forecast from the configured adapter."""
        try:
            from hemm.adapters.registry import get_registry

            registry = get_registry()
            adapter = registry.get(self._price_adapter)
            points = adapter.fetch(horizon_hours=self._horizon_hours)
            return [(p.timestamp, p.value) for p in points]
        except Exception:
            _LOGGER.warning("Price adapter '%s' failed, using flat price", self._price_adapter)
            now = dt_util.utcnow()
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
        from hemm.solvers.protocol import SolverResult, SolverStatus

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

        now = dt_util.utcnow()

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
                "timestamp": dt_util.utcnow().isoformat(),
                "status": result.status.value,
                "solver": self._solver_backend,
                "objective": result.objective_value,
                "solve_time": result.solve_time_seconds,
                "plan_count": len(result.plans),
            }
            self._dry_run_log.append(entry)
            self.hass.bus.async_fire(EVENT_DRY_RUN_COMPLETED, entry)
            return result

        # Apply results
        self._last_result = result
        self._iteration_count += 1
        if result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            self._previous_plans = result.plans

        # Record lambda history
        self._lambda_history.append(
            {
                "iteration": self._iteration_count,
                "timestamp": dt_util.utcnow().isoformat(),
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
                        "timestamp": dt_util.utcnow().isoformat(),
                        "device_id": device_id,
                        "device_type": device_type,
                        "updates": id_result.parameter_updates,
                        "confidence": id_result.confidence,
                        "message": id_result.message,
                    }
                )

        return results

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data — returns current config and cached solver results.

        This method must be fast and never block on solver execution.
        The solver is run separately via services or scheduled tasks.
        """
        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])

        # Build device_plans from cached solver result or stubs
        device_plans: dict[str, dict[str, Any]] = {}
        last_status = "idle"
        last_solve_time = 0.0
        last_plans: list[Any] = []

        if self._last_result is not None and _HEMM_CORE_AVAILABLE:
            try:
                from hemm.solvers.protocol import SolverStatus

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
                                "reason": slot.reason if hasattr(slot, "reason") else "idle",
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
        }
