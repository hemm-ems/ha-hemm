"""Tests for the HEMM options flow."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hemm.const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
)


@pytest.mark.unit
async def test_options_flow_shows_form(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that options flow shows a form."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.unit
async def test_options_flow_updates(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that options can be updated."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)

    new_options = {
        CONF_HORIZON_HOURS: 48,
        CONF_MAX_ITERATIONS: 100,
        CONF_PRICE_ADAPTER: "solcast",
        CONF_SOLVER_BACKEND: "distributed",
    }

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=new_options)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HORIZON_HOURS] == 48
    assert result["data"][CONF_SOLVER_BACKEND] == "distributed"
