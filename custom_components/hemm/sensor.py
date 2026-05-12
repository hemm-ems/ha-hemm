"""Sensor platform for HEMM — per-device plan, confidence, mode, and reason entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, CONF_DEVICE_TYPE, DOMAIN, PLAN_REASONS
from .coordinator import HemmCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HEMM sensors from device entries."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    devices: list[dict[str, Any]] = entry.data.get("devices", [])
    for device in devices:
        device_id = device.get("id", "unknown")
        device_name = device.get(CONF_DEVICE_NAME, "Unknown Device")
        device_type = device.get(CONF_DEVICE_TYPE, "unknown")

        entities.append(HemmPlanSensor(coordinator, entry, device_id, device_name, device_type))
        entities.append(HemmConfidenceSensor(coordinator, entry, device_id, device_name, device_type))
        entities.append(HemmModeSensor(coordinator, entry, device_id, device_name, device_type))
        entities.append(HemmReasonSensor(coordinator, entry, device_id, device_name, device_type))

    async_add_entities(entities)


class HemmPlanSensor(CoordinatorEntity[HemmCoordinator], SensorEntity):
    """Sensor showing the current plan (allocated power) for a device."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

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
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_plan"
        self._attr_name = f"{device_name} Plan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_id}")},
            "name": device_name,
            "manufacturer": "HEMM",
            "model": device_type,
            "via_device": (DOMAIN, entry.entry_id),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        plans = self.coordinator.data.get("device_plans", {}) if self.coordinator.data else {}
        self._attr_native_value = plans.get(self._device_id, {}).get("power_kw", 0.0)
        self.async_write_ha_state()


class HemmConfidenceSensor(CoordinatorEntity[HemmCoordinator], SensorEntity):
    """Sensor showing plan confidence for a device."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

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
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_confidence"
        self._attr_name = f"{device_name} Confidence"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_id}")},
            "name": device_name,
            "manufacturer": "HEMM",
            "model": device_type,
            "via_device": (DOMAIN, entry.entry_id),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        plans = self.coordinator.data.get("device_plans", {}) if self.coordinator.data else {}
        self._attr_native_value = plans.get(self._device_id, {}).get("confidence_pct", 0.0)
        self.async_write_ha_state()


class HemmModeSensor(CoordinatorEntity[HemmCoordinator], SensorEntity):
    """Sensor showing the current operating mode for a device."""

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
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_mode"
        self._attr_name = f"{device_name} Mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_id}")},
            "name": device_name,
            "manufacturer": "HEMM",
            "model": device_type,
            "via_device": (DOMAIN, entry.entry_id),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        plans = self.coordinator.data.get("device_plans", {}) if self.coordinator.data else {}
        self._attr_native_value = plans.get(self._device_id, {}).get("mode", "idle")
        self.async_write_ha_state()


class HemmReasonSensor(CoordinatorEntity[HemmCoordinator], SensorEntity):
    """Sensor showing why the current setpoint was chosen for a device."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PLAN_REASONS

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
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_reason"
        self._attr_name = f"{device_name} Reason"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{device_id}")},
            "name": device_name,
            "manufacturer": "HEMM",
            "model": device_type,
            "via_device": (DOMAIN, entry.entry_id),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        plans = self.coordinator.data.get("device_plans", {}) if self.coordinator.data else {}
        self._attr_native_value = plans.get(self._device_id, {}).get("reason", "idle")
        self.async_write_ha_state()
