"""Tests for the HEMM config flow."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_NAME,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
)


@pytest.mark.unit
async def test_config_flow_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test the user step shows the configuration form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.unit
async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    """Test creating a config entry with valid data."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    user_input = {
        CONF_NAME: "My HEMM",
        CONF_HORIZON_HOURS: 24,
        CONF_MAX_ITERATIONS: 50,
        CONF_PRICE_ADAPTER: "template",
        CONF_SOLVER_BACKEND: "milp_central",
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My HEMM"
    assert result["data"] == user_input


@pytest.mark.unit
async def test_config_flow_defaults(hass: HomeAssistant) -> None:
    """Test that defaults are applied correctly."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    # Submit with all defaults
    user_input = {
        CONF_NAME: DEFAULT_NAME,
        CONF_HORIZON_HOURS: DEFAULT_HORIZON_HOURS,
        CONF_MAX_ITERATIONS: DEFAULT_MAX_ITERATIONS,
        CONF_PRICE_ADAPTER: DEFAULT_PRICE_ADAPTER,
        CONF_SOLVER_BACKEND: DEFAULT_SOLVER_BACKEND,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HORIZON_HOURS] == 24
    assert result["data"][CONF_SOLVER_BACKEND] == "milp_central"


@pytest.mark.unit
async def test_config_flow_already_configured(hass: HomeAssistant) -> None:
    """Test aborting if already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: "HEMM",
            CONF_HORIZON_HOURS: 24,
            CONF_MAX_ITERATIONS: 50,
            CONF_PRICE_ADAPTER: "template",
            CONF_SOLVER_BACKEND: "milp_central",
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    user_input = {
        CONF_NAME: "Another HEMM",
        CONF_HORIZON_HOURS: 48,
        CONF_MAX_ITERATIONS: 100,
        CONF_PRICE_ADAPTER: "solcast",
        CONF_SOLVER_BACKEND: "distributed",
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=user_input)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.unit
async def test_config_flow_distributed_solver(hass: HomeAssistant) -> None:
    """Test creating entry with distributed solver."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    user_input = {
        CONF_NAME: "HEMM Distributed",
        CONF_HORIZON_HOURS: 12,
        CONF_MAX_ITERATIONS: 100,
        CONF_PRICE_ADAPTER: "forecast_solar",
        CONF_SOLVER_BACKEND: "distributed",
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOLVER_BACKEND] == "distributed"
    assert result["data"][CONF_PRICE_ADAPTER] == "forecast_solar"
