"""Pool pump thesis smoke test for the HA integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_MAX_POWER_KW,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_SOLVER_BACKEND,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
    SERVICE_ADD_CONSTRAINT,
    SERVICE_REPLAN,
    SERVICE_SET_PRICE_CURVE,
    DeviceType,
)
from custom_components.hemm.coordinator import HemmCoordinator

POOL_PUMP_ID = "pool_pump_1"


@pytest.fixture
def pool_pump_entry() -> MockConfigEntry:
    """Create a mock HEMM entry with one pool pump."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="HEMM",
        data={
            CONF_NAME: "HEMM",
            CONF_HORIZON_HOURS: 4,
            CONF_MAX_ITERATIONS: DEFAULT_MAX_ITERATIONS,
            CONF_PRICE_ADAPTER: DEFAULT_PRICE_ADAPTER,
            CONF_SOLVER_BACKEND: DEFAULT_SOLVER_BACKEND,
            "devices": [
                {
                    "id": POOL_PUMP_ID,
                    CONF_DEVICE_TYPE: DeviceType.POOL_PUMP,
                    CONF_DEVICE_NAME: "HEMM Pool Pump",
                    CONF_MAX_POWER_KW: 1.2,
                    CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_pool_pump_safe",
                },
            ],
        },
        unique_id=f"{DOMAIN}_pool_pump",
    )


@pytest.fixture
async def init_with_pool_pump(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    pool_pump_entry: MockConfigEntry,
) -> ConfigEntry:
    """Set up HEMM with a pool pump device."""
    pool_pump_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(pool_pump_entry.entry_id)
    await hass.async_block_till_done()
    return pool_pump_entry


def _pool_pump_plan(coordinator: HemmCoordinator) -> dict[str, Any]:
    """Return the serialized pool pump plan from coordinator data."""
    plans = coordinator.data.get("last_plans", []) if coordinator.data else []
    plan = next((p for p in plans if p["device_id"] == POOL_PUMP_ID), None)
    assert plan is not None
    assert plan["slots"]
    return plan


@pytest.mark.unit
@pytest.mark.req("003:FR-012")
async def test_pool_pump_replan_publishes_sensor_and_forbidden_window_zeroes_slots(
    hass: HomeAssistant,
    init_with_pool_pump: ConfigEntry,
) -> None:
    # REQ: 003:FR-012
    """REQ: 003:FR-012 - pool_pump plans end-to-end and respects lockout."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_pool_pump.entry_id]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_PRICE_CURVE,
        {
            "prices": [0.01, 0.01, 0.01, 0.01, *([0.30] * 12)],
            "resolution_minutes": 15,
        },
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_CONSTRAINT,
        {
            "window_id": "pool_pump_energy",
            "device_id": POOL_PUMP_ID,
            "deadline": (coordinator.clock.now() + timedelta(hours=4)).isoformat(),
            "requirement_type": "min_energy_until",
            "requirement_params": {"min_energy_kwh": 0.6},
        },
        blocking=True,
    )

    await hass.services.async_call(DOMAIN, SERVICE_REPLAN, blocking=True)
    await hass.async_block_till_done()

    initial_plan = _pool_pump_plan(coordinator)
    assert any(slot["power_kw"] > 0.01 for slot in initial_plan["slots"])

    plan_sensor = next(
        (
            state
            for state in hass.states.async_all("sensor")
            if state.entity_id.startswith("sensor.hemm_")
            and "pool_pump" in state.entity_id
            and "plan" in state.entity_id
        ),
        None,
    )
    assert plan_sensor is not None
    assert float(plan_sensor.state) >= 0.0

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_CONSTRAINT,
        {
            "window_id": "pool_pump_quiet_hour",
            "device_id": POOL_PUMP_ID,
            "deadline": (coordinator.clock.now() + timedelta(hours=1)).isoformat(),
            "requirement_type": "forbidden_window",
        },
        blocking=True,
    )
    await hass.services.async_call(DOMAIN, SERVICE_REPLAN, blocking=True)
    await hass.async_block_till_done()

    constrained_plan = _pool_pump_plan(coordinator)
    locked_slots = constrained_plan["slots"][:4]
    assert all(abs(slot["power_kw"]) < 0.01 for slot in locked_slots)
    assert all(slot["mode"] == "idle" for slot in locked_slots)
    assert any(slot["power_kw"] > 0.01 for slot in constrained_plan["slots"][4:])
