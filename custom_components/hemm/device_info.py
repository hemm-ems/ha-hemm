"""Device-registry helpers shared by the HEMM entity platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# HA 2026.8 added DeviceInfo["via_device_id"] (a registry device id) and deprecated the
# identifier-tuple ``via_device`` (removed in 2027.8). Older cores only know the tuple.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def async_register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Register the HEMM hub device before any platform links child devices to it.

    Returns its registry id: HA 2026.10 deprecates looking it up again by identifier.
    """
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="HEMM",
    )
    return hub.id


def child_device_info(
    entry: ConfigEntry, hub_device_id: str | None, device_id: str, device_name: str, device_type: str
) -> DeviceInfo:
    """Return the device info for a managed device, linked to the hub device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{device_id}")},
        name=device_name,
        manufacturer="HEMM",
        model=device_type,
    )
    if not _HAS_VIA_DEVICE_ID:
        info["via_device"] = (DOMAIN, entry.entry_id)
    elif hub_device_id is not None:
        info["via_device_id"] = hub_device_id
    return info
