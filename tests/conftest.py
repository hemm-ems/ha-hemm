"""Pytest configuration for ha-hemm tests."""

from __future__ import annotations

import sys

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
)


def pytest_configure(config: pytest.Config) -> None:
    """Disable pytest-socket on Windows (ProactorEventLoop needs real sockets)."""
    if sys.platform == "win32":
        import pytest_socket

        pytest_socket.enable_socket()
        # Prevent re-disabling by patching the disable function
        pytest_socket.disable_socket = lambda *a, **kw: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry for HEMM."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="HEMM",
        data={
            CONF_NAME: "HEMM",
            CONF_HORIZON_HOURS: DEFAULT_HORIZON_HOURS,
            CONF_MAX_ITERATIONS: DEFAULT_MAX_ITERATIONS,
            CONF_PRICE_ADAPTER: DEFAULT_PRICE_ADAPTER,
            CONF_SOLVER_BACKEND: DEFAULT_SOLVER_BACKEND,
        },
        unique_id=DOMAIN,
    )


@pytest.fixture
async def init_integration(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> ConfigEntry:
    """Set up the HEMM integration in Home Assistant."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
