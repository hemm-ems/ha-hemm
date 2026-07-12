"""RW1 realism-tier tests — the solver sees the real home (003:FR-101..106, FR-503).

These assert what actually reaches ``solve()``: a live price series (not a flat
synthetic curve), a real PV generation overlay, and a measured battery SoC
initial state — plus that a broken price source raises a repair and refuses to
optimize on synthetic data (FR-102). The solver is stubbed to a recorder so the
tests stay fast and deterministic; the physics is covered in the core repo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_CAPACITY_KWH,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_FORECAST_ADAPTER,
    CONF_FORECAST_ENTITY,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_NAME,
    CONF_PEAK_POWER_KWP,
    CONF_PLUG_STATE_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_SOC_ENTITY,
    DOMAIN,
)
from custom_components.hemm.coordinator import HemmCoordinator
from custom_components.hemm.device_flow import _build_ev_charger_schema
from custom_components.hemm.repairs import ISSUE_PRICE_UNAVAILABLE
from custom_components.hemm.time import HAClock

T0 = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)


class _FrozenClock(HAClock):
    """Deterministic Clock — freezes ``now()`` so horizon anchoring is testable.

    A live tariff series starts at 00:00 today; the coordinator drops elapsed slots
    relative to ``now()``. Without a fixed clock these tests would depend on wall time.
    """

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _RecordingSolver:
    """Stub solver that records the kwargs it was handed."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def solve(
        self,
        manifests: list[Any],
        constraint_windows: list[Any],
        price_forecast: list[tuple[datetime, float]],
        horizon_minutes: int,
        resolution_minutes: int,
        previous_plans: list[Any] | None = None,
        weather_forecast: Any = None,
        generation_forecast: dict[str, list[float]] | None = None,
        initial_state: dict[str, dict[str, float]] | None = None,
    ) -> Any:
        from hemm_core.solvers.protocol import SolverResult, SolverStatus

        self.calls.append(
            {
                "price_forecast": price_forecast,
                "generation_forecast": generation_forecast,
                "initial_state": initial_state,
            }
        )
        return SolverResult(status=SolverStatus.OPTIMAL, plans=[])


def _battery(soc_entity: str | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        "id": "battery_1",
        CONF_DEVICE_TYPE: "battery",
        CONF_DEVICE_NAME: "Home Battery",
        CONF_SAFE_DEFAULT_SCRIPT: "script.battery_safe",
        CONF_CAPACITY_KWH: 9.0,
        CONF_MAX_CHARGE_KW: 4.0,
        CONF_MAX_DISCHARGE_KW: 4.0,
    }
    if soc_entity:
        device[CONF_SOC_ENTITY] = soc_entity
    return device


def _pv(forecast_entity: str | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        "id": "pv_1",
        CONF_DEVICE_TYPE: "pv_forecast",
        CONF_DEVICE_NAME: "Rooftop PV",
        CONF_SAFE_DEFAULT_SCRIPT: "script.pv_safe",
        CONF_PEAK_POWER_KWP: 7.0,
        CONF_FORECAST_ADAPTER: "forecast_solar",
    }
    if forecast_entity:
        device[CONF_FORECAST_ENTITY] = forecast_entity
    return device


def _make_coordinator(
    hass: HomeAssistant, devices: list[dict[str, Any]], *, now: datetime = T0, **hub: Any
) -> HemmCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HEMM",
        data={CONF_NAME: "HEMM", "devices": devices, **hub},
        unique_id=f"{DOMAIN}_rw1",
    )
    entry.add_to_hass(hass)
    # Freeze the clock at the price curve's start by default, so the anchoring slice is
    # a no-op for tests that assert on the full positional series.
    return HemmCoordinator(hass, entry, clock=_FrozenClock(now))


def _set_price_entity(hass: HomeAssistant, entity_id: str, values: list[float]) -> None:
    """Publish a tariff sensor with a Nordpool-style raw_today forward curve."""
    raw = [{"start": (T0 + timedelta(hours=i)).isoformat(), "value": v} for i, v in enumerate(values)]
    hass.states.async_set(entity_id, str(values[0]), {"raw_today": raw})


@pytest.mark.unit
@pytest.mark.req("003:FR-101")
async def test_price_entity_series_reaches_solve(hass: HomeAssistant) -> None:
    """A configured price entity's curve reaches solve() — not a flat synthetic price."""
    coordinator = _make_coordinator(hass, [_battery()], **{CONF_PRICE_ENTITY: "sensor.tariff"})
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    _set_price_entity(hass, "sensor.tariff", [0.10, 0.30, 0.05, 0.25])

    await coordinator.async_run_solver()

    assert rec.calls, "solve() should have run with a real price series"
    prices = [v for _, v in rec.calls[0]["price_forecast"]]
    assert prices[:4] == [0.10, 0.30, 0.05, 0.25]
    assert len(set(prices)) > 1, "price curve must vary, not be a flat fallback"


@pytest.mark.unit
@pytest.mark.req("003:FR-101")
async def test_horizon_anchored_to_current_slot(hass: HomeAssistant) -> None:
    """The solve is anchored at the current slot, not the price curve's 00:00 start.

    Regression: a live tariff series begins at 00:00 today and the MILP aligns prices
    positionally (slot ``i`` == ``forecast[i]``) and stamps the plan from
    ``forecast[0][0]``. Passing the raw series anchored the solve at midnight — it
    planned the elapsed part of the day and applied the measured SoC/temperature at
    00:00. The coordinator must drop the elapsed slots first so ``forecast[0]`` is the
    slot containing ``now``.
    """
    coordinator = _make_coordinator(
        hass, [_battery("sensor.batt_soc")], now=T0 + timedelta(hours=3), **{CONF_PRICE_ENTITY: "sensor.tariff"}
    )
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    # A distinct price per hour of the whole day, starting at 00:00.
    _set_price_entity(hass, "sensor.tariff", [round(0.10 + 0.01 * i, 2) for i in range(24)])
    hass.states.async_set("sensor.batt_soc", "40")  # percent, measured "now" (03:00)

    await coordinator.async_run_solver()

    assert rec.calls
    fc = rec.calls[0]["price_forecast"]
    # First horizon slot is 03:00 (the current slot) carrying the hour-3 price -- the
    # 00:00 to 02:00 slots are dropped, not fed to the solver as "the plan".
    assert fc[0][0] == T0 + timedelta(hours=3)
    assert fc[0][1] == pytest.approx(0.13)
    assert all(ts >= T0 + timedelta(hours=3) for ts, _ in fc)
    # The measured SoC is the start-of-horizon (03:00) state, not a midnight state.
    assert rec.calls[0]["initial_state"]["battery_1"]["soc_kwh"] == pytest.approx(9.0 * 0.40)


@pytest.mark.unit
@pytest.mark.req("003:FR-102")
async def test_missing_price_raises_repair_and_skips_solve(hass: HomeAssistant) -> None:
    """A configured-but-unavailable price entity → repair issue + no synthetic solve."""
    coordinator = _make_coordinator(hass, [_battery()], **{CONF_PRICE_ENTITY: "sensor.tariff"})
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    # Note: sensor.tariff is never published -> unreadable.

    await coordinator.async_run_solver()

    assert not rec.calls, "solve() must not run on synthetic data when the price source fails"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, ISSUE_PRICE_UNAVAILABLE) is not None


@pytest.mark.unit
@pytest.mark.req("003:FR-102")
async def test_price_recovery_clears_repair(hass: HomeAssistant) -> None:
    """Once a real price series returns, the price-unavailable repair is cleared."""
    coordinator = _make_coordinator(hass, [_battery()], **{CONF_PRICE_ENTITY: "sensor.tariff"})
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    registry = ir.async_get(hass)

    await coordinator.async_run_solver()  # broken -> issue raised
    assert registry.async_get_issue(DOMAIN, ISSUE_PRICE_UNAVAILABLE) is not None

    _set_price_entity(hass, "sensor.tariff", [0.10, 0.30])
    await coordinator.async_run_solver()  # recovered -> issue cleared
    assert registry.async_get_issue(DOMAIN, ISSUE_PRICE_UNAVAILABLE) is None
    assert rec.calls


@pytest.mark.unit
@pytest.mark.req("003:FR-103")
async def test_pv_forecast_reaches_generation_overlay(hass: HomeAssistant) -> None:
    """A configured forecast entity's series reaches solve() as a non-zero generation overlay."""
    coordinator = _make_coordinator(hass, [_pv("sensor.pv_fc")], **{CONF_PRICE_ENTITY: "sensor.tariff"})
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    _set_price_entity(hass, "sensor.tariff", [0.20] * 6)
    # Forecast.Solar-style watts dict (W), aligned to the price horizon start.
    watts = {(T0 + timedelta(hours=i)).isoformat(): w for i, w in enumerate([0, 0, 1200, 3400, 2100, 0])}
    hass.states.async_set("sensor.pv_fc", "3400", {"watts": watts})

    await coordinator.async_run_solver()

    assert rec.calls
    gen = rec.calls[0]["generation_forecast"]
    assert gen is not None and "pv_1" in gen
    assert max(gen["pv_1"]) > 0, "PV overlay must be non-zero"
    # Peak ~3.4 kW (watts / 1000), step-held across the 15-min slots of that hour.
    assert 3.0 <= max(gen["pv_1"]) <= 3.5


@pytest.mark.unit
@pytest.mark.req("003:FR-105")
async def test_battery_soc_reaches_initial_state(hass: HomeAssistant) -> None:
    """A configured SoC entity (%) becomes initial_state[dev]['soc_kwh'] = capacity * pct/100."""
    coordinator = _make_coordinator(hass, [_battery("sensor.batt_soc")], **{CONF_PRICE_ENTITY: "sensor.tariff"})
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]
    _set_price_entity(hass, "sensor.tariff", [0.20, 0.20])
    hass.states.async_set("sensor.batt_soc", "62")  # percent

    await coordinator.async_run_solver()

    assert rec.calls
    initial = rec.calls[0]["initial_state"]
    assert initial is not None and "battery_1" in initial
    assert initial["battery_1"]["soc_kwh"] == pytest.approx(9.0 * 0.62)


@pytest.mark.unit
@pytest.mark.req("003:FR-102")
async def test_no_price_entity_no_synthetic_solve(hass: HomeAssistant) -> None:
    """Default template adapter with no price entity produces nothing → skip, never flat 0.30."""
    coordinator = _make_coordinator(hass, [_battery()])  # no price entity, template default
    rec = _RecordingSolver()
    coordinator._get_solver = lambda *a, **k: rec  # type: ignore[assignment]

    await coordinator.async_run_solver()

    assert not rec.calls, "the template default must not fabricate a flat price"


@pytest.mark.unit
@pytest.mark.req("003:FR-503")
def test_ev_plug_state_accepts_sensor_domain() -> None:
    """EV plug/charge-state selector accepts a sensor domain (go-e car-status is a sensor)."""
    schema = _build_ev_charger_schema("pro")
    selector = None
    for marker in schema.schema:
        if getattr(marker, "schema", marker) == CONF_PLUG_STATE_ENTITY:
            selector = schema.schema[marker]
            break
    assert selector is not None, "EV pro schema must expose plug_state_entity"
    domain = selector.config["domain"]
    assert "sensor" in domain and "binary_sensor" in domain
    # And a plain vol.Schema build with a sensor entity must validate.
    assert isinstance(schema, vol.Schema)
