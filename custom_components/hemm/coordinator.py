"""DataUpdateCoordinator for HEMM — runs optimization on schedule."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .actuator import ActuationDecision, ActuatorEngine
from .const import (
    CONF_ACTUATION_ENABLED,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CAPACITY_KWH,
    CONF_DEVICE_TYPE,
    CONF_FEED_IN_TARIFF,
    CONF_FORECAST_ENTITY,
    CONF_FORECAST_ENTITY_2,
    CONF_GRID_EXPORT_LIMIT_KW,
    CONF_GRID_IMPORT_LIMIT_KW,
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_PRICE_ADAPTER,
    CONF_PRICE_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SOLVER_BACKEND,
    CONF_TEMP_ENTITY,
    CONF_WATCHDOG_TIMEOUT_SECONDS,
    CONF_WEATHER_ENTITY,
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
    DeviceType,
)
from .identification import IdentificationResult, get_identifier
from .manifest_builder import build_all_manifests
from .repairs import (
    async_clear_price_unavailable_issue,
    async_create_price_unavailable_issue,
)
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

# Process-wide solve lock: concurrent pyomo/HiGHS solves in separate executor
# threads deadlock in native code. A config-entry reload briefly leaves the old
# coordinator's background solve running alongside the new instance, so the
# lock must be shared across coordinator instances, not per-instance.
_SOLVE_LOCK = asyncio.Lock()

UPDATE_INTERVAL = timedelta(minutes=15)
SOLVER_TIMEOUT_SECONDS = UPDATE_INTERVAL.total_seconds() - 30
MAX_HISTORY = 20

# Entity states that mean "no real reading" — never fabricate a value from these.
_UNAVAILABLE_STATES = ("unknown", "unavailable", "none", "")

# ── Live price parsing (FR-101) ───────────────────────────────────────────────
# Real tariff integrations expose the forward price curve under integration-
# specific attributes (Nordpool: raw_today/raw_tomorrow; Tibber-style: today/
# tomorrow; EPEX/ENTSO-e: data/forecast). We scan a prioritized set of shapes
# and normalize to the pre-fetched-series contract the adapters already accept:
# data=[{"timestamp": iso, "value": eur_per_kwh, "unit": "EUR_per_kWh"}].
_PRICE_LIST_ATTR_GROUPS: tuple[tuple[str, ...], ...] = (
    ("raw_today", "raw_tomorrow"),
    ("today", "tomorrow"),
    ("prices_today", "prices_tomorrow"),
    ("forecast",),
    ("prices",),
    ("data",),
    ("raw",),
    ("price_forecast",),
)
_PRICE_TS_KEYS = ("start", "startsAt", "start_time", "startTime", "from", "time", "datetime", "timestamp", "hour")
_PRICE_VALUE_KEYS = ("total", "value", "price", "cost", "amount", "EUR_per_kWh")

# ── Live PV forecast parsing (FR-103) ─────────────────────────────────────────
_PV_TS_KEYS = ("period_start", "start", "startsAt", "datetime", "time", "timestamp")


def _to_dt(value: object) -> datetime | None:
    """Parse a timestamp (datetime or ISO string) into a tz-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return None


def _to_float(value: object, scale: float = 1.0) -> float | None:
    try:
        return float(value) * scale  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_price_series(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract a forward price curve from a tariff entity's attributes.

    Returns the pre-fetched-series list the price adapters consume, or ``[]``
    when no usable forward curve is present (the coordinator then refuses to
    optimize on synthetic data — FR-102).
    """
    for group in _PRICE_LIST_ATTR_GROUPS:
        items: list[Any] = []
        for key in group:
            val = attributes.get(key)
            if isinstance(val, list):
                items.extend(val)
        series = _parse_price_items(items)
        if series:
            return series
    return []


def _parse_price_items(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = next((item[k] for k in _PRICE_TS_KEYS if k in item), None)
        raw_val = next((item[k] for k in _PRICE_VALUE_KEYS if item.get(k) is not None), None)
        iso = _to_dt(ts)
        val = _to_float(raw_val)
        if iso is None or val is None:
            continue
        out.append({"timestamp": iso.isoformat(), "value": val, "unit": "EUR_per_kWh"})
    out.sort(key=lambda p: p["timestamp"])
    return out


def _extract_pv_series(attributes: Mapping[str, Any]) -> list[tuple[datetime, float]]:
    """Extract a (timestamp, kW) PV forecast from a solar-forecast entity.

    Handles Forecast.Solar's ``watts`` dict (iso -> W) and the list-of-dict
    detailed-forecast shape used by Solcast and similar (kW or W per slot).
    Returns ``[]`` when no per-slot curve is present.
    """
    watts = attributes.get("watts")
    if isinstance(watts, dict):
        pts = [(dt, kw) for k, v in watts.items() if (dt := _to_dt(k)) and (kw := _to_float(v, 0.001)) is not None]
        if pts:
            pts.sort(key=lambda p: p[0])
            return pts
    for key in ("detailedForecast", "detailedHourly", "forecast", "wattsList"):
        val = attributes.get(key)
        if isinstance(val, list):
            pts = _parse_pv_items(val)
            if pts:
                return pts
    return []


def _parse_pv_items(items: list[Any]) -> list[tuple[datetime, float]]:
    out: list[tuple[datetime, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dt = _to_dt(next((item[k] for k in _PV_TS_KEYS if k in item), None))
        if dt is None:
            continue
        if "pv_estimate" in item:
            kw = _to_float(item["pv_estimate"])
        elif "power_kw" in item:
            kw = _to_float(item["power_kw"])
        elif "power_w" in item:
            kw = _to_float(item["power_w"], 0.001)
        elif "watts" in item:
            kw = _to_float(item["watts"], 0.001)
        elif "value" in item:
            kw = _to_float(item["value"])
        elif "power" in item:
            kw = _to_float(item["power"])
        else:
            kw = None
        if kw is None:
            continue
        out.append((dt, kw))
    out.sort(key=lambda p: p[0])
    return out


def _resample_to_slots(
    series: list[tuple[datetime, float]], t0: datetime, n_slots: int, resolution_minutes: int
) -> list[float]:
    """Step-hold-resample an hourly (timestamp, value) series onto solver slots."""
    if not series:
        return [0.0] * n_slots
    values: list[float] = []
    j = 0
    for i in range(n_slots):
        slot_t = t0 + timedelta(minutes=i * resolution_minutes)
        while j + 1 < len(series) and series[j + 1][0] <= slot_t:
            j += 1
        # Before the first known point, production is unknown -> 0 (never negative).
        values.append(0.0 if slot_t < series[0][0] else max(0.0, series[j][1]))
    return values


def _slice_forecast_from_now(forecast: list[tuple[datetime, float]], now: datetime) -> list[tuple[datetime, float]]:
    """Drop already-elapsed slots so ``forecast[0]`` is the slot containing ``now``.

    The MILP aligns prices to slots positionally (slot ``i`` is ``forecast[i]``) and
    stamps the plan from ``forecast[0][0]``. A live tariff series begins at 00:00
    today, so passing it raw anchors the solve at midnight — it plans the elapsed part
    of the day and applies the measured start-of-horizon state (SoC, temperature) at
    00:00 instead of the current slot. Keep the last point at or before ``now`` (the
    current slot) and everything after it. A no-op when the series already starts at
    ``now`` (e.g. a manual price curve). Assumes ``forecast`` is sorted ascending.
    """
    start = 0
    for i, (timestamp, _value) in enumerate(forecast):
        if timestamp <= now:
            start = i
        else:
            break
    return forecast[start:]


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
        # Live-data spine (RW1): the real tariff / weather sources and economics.
        self._price_entity: str | None = entry.options.get(CONF_PRICE_ENTITY, entry.data.get(CONF_PRICE_ENTITY)) or None
        self._weather_entity: str | None = (
            entry.options.get(CONF_WEATHER_ENTITY, entry.data.get(CONF_WEATHER_ENTITY)) or None
        )
        self._feed_in_tariff: float | None = _to_float(
            entry.options.get(CONF_FEED_IN_TARIFF, entry.data.get(CONF_FEED_IN_TARIFF))
        )
        # Grid/main-fuse connection limits (FR-201): None = unbounded (legacy).
        self._grid_import_limit_kw: float | None = _to_float(
            entry.options.get(CONF_GRID_IMPORT_LIMIT_KW, entry.data.get(CONF_GRID_IMPORT_LIMIT_KW))
        )
        self._grid_export_limit_kw: float | None = _to_float(
            entry.options.get(CONF_GRID_EXPORT_LIMIT_KW, entry.data.get(CONF_GRID_EXPORT_LIMIT_KW))
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
        self._solve_lock = _SOLVE_LOCK

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

    def _get_solver(self, feed_in_tariff: float | None = None, outdoor_temp_c: float | None = None) -> Any:
        """Create a solver instance for the active backend with real economics/physics."""
        if self._solver_backend == "distributed":
            from hemm_core.solvers.distributed import DistributedSolver

            return DistributedSolver(max_iterations=self._max_iterations, clock=self._clock)

        from hemm_core.solvers.milp_central import MILPCentralSolver

        kwargs: dict[str, Any] = {"clock": self._clock}
        if feed_in_tariff is not None:
            kwargs["feed_in_tariff"] = feed_in_tariff
        if outdoor_temp_c is not None:
            kwargs["outdoor_temp_c"] = outdoor_temp_c
        # FR-201: the configured connection/fuse limit bounds every solve.
        if self._grid_import_limit_kw is not None:
            kwargs["grid_import_limit_kw"] = self._grid_import_limit_kw
        if self._grid_export_limit_kw is not None:
            kwargs["grid_export_limit_kw"] = self._grid_export_limit_kw
        return MILPCentralSolver(**kwargs)

    def _get_price_forecast(
        self, price_data: list[dict[str, Any]] | None = None
    ) -> list[tuple[datetime, float]] | None:
        """Fetch the price forecast from manual override or the configured adapter.

        FR-102: there is no silent flat fallback. Returns ``None`` when no real
        price series can be produced, and the caller skips the solve rather than
        optimize on synthetic data.
        """
        now = self._clock.now()

        # Prefer manual prices set via hemm.set_price_curve
        if self._manual_prices is not None:
            res_min = self._manual_price_resolution
            _LOGGER.info("Using manual price curve (%d slots, %d min resolution)", len(self._manual_prices), res_min)
            return [(now + timedelta(minutes=i * res_min), p) for i, p in enumerate(self._manual_prices)]

        # FR-102 (empty-price-entity edge): the price role only accepts a real,
        # pre-fetched series — the configured price entity's curve (data=) or the
        # manual override handled above. A self-fetching adapter mis-set as the
        # price source (solcast/forecast_solar) must never synthesize a curve.
        if not price_data:
            return None
        try:
            from hemm_core.adapters.registry import get_registry

            registry = get_registry()
            adapter = registry.get(self._price_adapter)
            points = adapter.fetch(data=price_data)
        except Exception:
            _LOGGER.warning("Price adapter '%s' failed to produce a forecast", self._price_adapter)
            return None
        if not points:
            return None
        return [(p.timestamp, p.value) for p in points]

    def _read_entity_float(self, entity_id: str | None) -> float | None:
        """Read an entity's numeric state, or None if missing/unavailable/non-numeric."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or str(state.state).lower() in _UNAVAILABLE_STATES:
            return None
        return _to_float(state.state)

    def _read_price_series(self) -> list[dict[str, Any]] | None:
        """Read the configured price entity's forward curve (event loop). FR-101.

        Returns the pre-fetched-series list for the adapter, or None when the
        entity is missing/unavailable or exposes no usable forward price curve.
        """
        if not self._price_entity:
            return None
        state = self.hass.states.get(self._price_entity)
        if state is None or str(state.state).lower() in _UNAVAILABLE_STATES:
            return None
        return _extract_price_series(state.attributes) or None

    def _read_outdoor_temp(self) -> float | None:
        """Read current outdoor temperature from the configured weather entity."""
        if not self._weather_entity:
            return None
        state = self.hass.states.get(self._weather_entity)
        if state is None or str(state.state).lower() in _UNAVAILABLE_STATES:
            return None
        # weather.* entities carry temperature as an attribute; a plain sensor as state.
        temp = state.attributes.get("temperature")
        return _to_float(temp) if temp is not None else _to_float(state.state)

    def _build_generation_forecast(
        self, devices: list[dict[str, Any]], n_slots: int, resolution_minutes: int, t0: datetime
    ) -> dict[str, list[float]]:
        """Build per-device per-slot kW generation series from configured forecast entities. FR-103."""
        forecast: dict[str, list[float]] = {}
        for device in devices:
            if device.get(CONF_DEVICE_TYPE) != DeviceType.PV_FORECAST:
                continue
            # Merge the series of both forecast entities by timestamp: e.g.
            # Solcast's today + tomorrow entities, so the 24 h horizon keeps a
            # real PV curve after today's series runs out at midnight.
            series: list[tuple[datetime, float]] = []
            for key in (CONF_FORECAST_ENTITY, CONF_FORECAST_ENTITY_2):
                entity_id = device.get(key)
                if not entity_id:
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or str(state.state).lower() in _UNAVAILABLE_STATES:
                    continue
                series.extend(_extract_pv_series(state.attributes))
            if not series:
                continue
            series.sort(key=lambda point: point[0])
            slots = _resample_to_slots(series, t0, n_slots, resolution_minutes)
            if any(v > 0 for v in slots):
                forecast[device["id"]] = slots
        return forecast

    def _build_initial_state(self, devices: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
        """Build per-device measured start state (SoC kWh, temperature °C). FR-104/105."""
        state: dict[str, dict[str, float]] = {}
        for device in devices:
            device_id = device.get("id")
            if not device_id:
                continue
            entry: dict[str, float] = {}
            device_type = device.get(CONF_DEVICE_TYPE)
            if device_type in (DeviceType.BATTERY, DeviceType.EV_CHARGER):
                soc_pct = self._read_entity_float(device.get(CONF_SOC_ENTITY))
                capacity = device.get(CONF_CAPACITY_KWH) or device.get(CONF_BATTERY_CAPACITY_KWH)
                if soc_pct is not None and capacity:
                    # SoC entity reports percent; the solver wants stored energy in kWh.
                    entry["soc_kwh"] = float(capacity) * soc_pct / 100.0
            temp_c = self._read_entity_float(device.get(CONF_TEMP_ENTITY))
            if temp_c is not None:
                entry["temp_c"] = temp_c
            if entry:
                state[device_id] = entry
        return state

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
        # Serialize solves instead of skipping: an explicit hemm.replan must
        # yield a fresh solve even when a background solve is in flight (the
        # old skip-and-return-cached path made replan a silent no-op, exposed
        # once the pyomo import stopped blocking the event loop).
        if self._solve_lock.locked():
            _LOGGER.debug("Solver already running, waiting for it to finish")
        async with self._solve_lock:
            return await self._do_solve(dry_run=dry_run, device_filter=device_filter)

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

        # FR-101/102: read the real tariff on the event loop and refuse to optimize
        # on synthetic data. A configured-but-unreadable price entity, or an adapter
        # that produces nothing, raises a repair issue and skips the solve — leaving
        # the previous plan and _last_result untouched (no silent flat fallback).
        price_data = self._read_price_series()
        if self._price_entity and price_data is None:
            _LOGGER.warning("Price entity '%s' unavailable; skipping solve (no synthetic fallback)", self._price_entity)
            await async_create_price_unavailable_issue(self.hass)
            return SolverResult(status=SolverStatus.ERROR, diagnostics={"error": "price_unavailable"})

        price_forecast = await self.hass.async_add_executor_job(self._get_price_forecast, price_data)
        if not price_forecast:
            _LOGGER.warning(
                "Price adapter '%s' produced no forecast; skipping solve (no synthetic fallback)", self._price_adapter
            )
            await async_create_price_unavailable_issue(self.hass)
            return SolverResult(status=SolverStatus.ERROR, diagnostics={"error": "price_unavailable"})
        async_clear_price_unavailable_issue(self.hass)

        # RW1 live state: measured SoC/temperature + real PV production, plus real
        # economics (feed-in tariff, outdoor temperature) on the solver itself.
        resolution_minutes = 15
        n_slots = self._horizon_hours * (60 // resolution_minutes)
        # Anchor the horizon at the current slot before it reaches the solver: a live
        # tariff series starts at 00:00 today and the MILP aligns prices/plan
        # positionally, so the raw series would anchor the solve at midnight (planning
        # the elapsed day and applying the measured SoC/temperature at 00:00).
        price_forecast = _slice_forecast_from_now(price_forecast, now)
        t0 = _to_dt(price_forecast[0][0]) or now
        generation_forecast = self._build_generation_forecast(devices, n_slots, resolution_minutes, t0) or None
        initial_state = self._build_initial_state(devices) or None
        outdoor_temp_c = self._read_outdoor_temp()

        # Solver construction imports pyomo (heavy); keep it off the event loop.
        solver = await self.hass.async_add_import_executor_job(self._get_solver, self._feed_in_tariff, outdoor_temp_c)

        result: SolverResult = await self.hass.async_add_executor_job(
            solver.solve,
            manifests,
            active_windows,
            price_forecast,
            self._horizon_hours * 60,
            resolution_minutes,
            self._previous_plans if self._previous_plans else None,
            None,  # weather_forecast: per-slot series not wired here (gs heat is gas)
            generation_forecast,
            initial_state,
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
        solver runs are serialized by the _solve_lock (the periodic tick skips
        scheduling when a solve is already in flight; explicit service calls wait).
        """
        devices: list[dict[str, Any]] = self.config_entry.data.get("devices", [])

        # Schedule solver as a non-blocking background task
        if devices and _HEMM_CORE_AVAILABLE and not self._solve_lock.locked():
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
                                # FR-501: the full forward plan, so the shadow is inspectable.
                                "schedule": [
                                    {
                                        "start": s.start.isoformat(),
                                        "power_kw": s.power_kw,
                                        "mode": s.mode or "active",
                                        "reason": s.reason.value if s.reason else "idle",
                                    }
                                    for s in plan.slots
                                ],
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
