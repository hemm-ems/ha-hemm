"""Diagnostics support for HEMM."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, TESTED_HA_VERSION
from .coordinator import HemmCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    return {
        "tested_ha_version": TESTED_HA_VERSION,
        "config_entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator_state": {
            "horizon_hours": coordinator.horizon_hours,
            "solver_backend": coordinator.solver_backend,
            "price_adapter": coordinator.price_adapter,
            "last_plans": data.get("last_plans", []),
            "iteration_count": data.get("iteration_count", 0),
        },
    }
