"""Switch platform for HEMM device overrides."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, CONF_DEVICE_TYPE, DOMAIN
from .coordinator import HemmCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up per-device HEMM override switches."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for device in entry.data.get("devices", []):
        device_id = device.get("id", "unknown")
        device_name = device.get(CONF_DEVICE_NAME, "Unknown Device")
        device_type = device.get(CONF_DEVICE_TYPE, "unknown")
        entities.append(HemmOverrideSwitch(coordinator, entry, device_id, device_name, device_type))
    async_add_entities(entities)


class HemmOverrideSwitch(CoordinatorEntity[HemmCoordinator], SwitchEntity):
    """Switch that suspends HEMM actuation for one device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HemmCoordinator,
        entry: ConfigEntry,
        device_id: str,
        device_name: str,
        device_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_override"
        self._attr_name = f"{device_name} Override"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_id}")},
            "name": device_name,
            "manufacturer": "HEMM",
            "model": device_type,
            "via_device": (DOMAIN, entry.entry_id),
        }

    @property
    def is_on(self) -> bool:
        """Return whether the override is active."""
        return self.coordinator.actuator.is_override_enabled(self._device_id)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return non-PII metadata useful for diagnostics and tests."""
        return {"hemm_device_id": self._device_id}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this device override."""
        self.coordinator.actuator.set_override(self._device_id, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this device override."""
        self.coordinator.actuator.set_override(self._device_id, False)
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates."""
        self.async_write_ha_state()
