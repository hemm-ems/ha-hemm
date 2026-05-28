"""HEMM — Distributed Energy Optimizer for Home Automation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import DOMAIN
from .coordinator import HemmCoordinator
from .services import async_register_services, async_unregister_services

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HEMM from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = HemmCoordinator(hass, entry)
    coordinator.async_set_updated_data(coordinator._build_data())

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once for the first entry)
    if len(hass.data[DOMAIN]) == 1:
        await async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a HEMM config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Unregister services if last entry removed
        if not hass.data[DOMAIN]:
            await async_unregister_services(hass)
    return unload_ok
