"""Tests for HEMM diagnostics."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.hemm.const import TESTED_HA_VERSION
from custom_components.hemm.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.unit
async def test_diagnostics_content(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test diagnostics returns expected structure."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diag["tested_ha_version"] == TESTED_HA_VERSION
    assert "config_entry" in diag
    assert diag["config_entry"]["title"] == "HEMM"
    assert "coordinator_state" in diag
    assert diag["coordinator_state"]["solver_backend"] == "milp_central"
    assert diag["coordinator_state"]["horizon_hours"] == 24


@pytest.mark.unit
async def test_diagnostics_tested_ha_version(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that tested_ha_version is present and non-empty."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)
    assert isinstance(diag["tested_ha_version"], str)
    assert len(diag["tested_ha_version"]) > 0
