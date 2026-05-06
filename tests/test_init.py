"""Tests for HEMM integration setup and coordinator."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.hemm.const import DOMAIN
from custom_components.hemm.coordinator import HemmCoordinator


@pytest.mark.unit
async def test_setup_entry(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test successful setup of config entry."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert init_integration.entry_id in hass.data[DOMAIN]


@pytest.mark.unit
async def test_coordinator_created(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that coordinator is created on setup."""
    coordinator = hass.data[DOMAIN][init_integration.entry_id]
    assert isinstance(coordinator, HemmCoordinator)


@pytest.mark.unit
async def test_coordinator_properties(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test coordinator exposes correct properties."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
    assert coordinator.horizon_hours == 24
    assert coordinator.solver_backend == "milp_central"
    assert coordinator.price_adapter == "template"


@pytest.mark.unit
async def test_coordinator_data(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test coordinator data after first refresh."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
    assert coordinator.data is not None
    assert coordinator.data["horizon_hours"] == 24
    assert coordinator.data["solver_backend"] == "milp_central"
    assert coordinator.data["last_plans"] == []
    assert coordinator.data["iteration_count"] == 0


@pytest.mark.unit
async def test_unload_entry(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test unloading a config entry."""
    assert init_integration.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert init_integration.entry_id not in hass.data[DOMAIN]
