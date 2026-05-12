"""Device config flows for HEMM — tiered configuration for all 7 manifest types.

Each of the 7 manifest types gets configurable via the options flow with
beginner/advanced/pro tiers. Beginner mode maps simple inputs to full
manifest values with documented defaults.

Implementation note: HA 2024.12 does not have ConfigSubentryFlow. Devices are
stored as a list in the config entry data under the "devices" key. The options
flow provides steps to add devices (select_device -> configure_device).
"""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_AZIMUTH_DEG,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_CONTROL_CLASS,
    CONF_DEFROST_LOCKOUT_MIN,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_DISCHARGE_EFFICIENCY,
    CONF_FLOOR_AREA_M2,
    CONF_FORECAST_ADAPTER,
    CONF_FORECAST_ENTITY,
    CONF_HYSTERESIS_K,
    CONF_INSULATION_CLASS,
    CONF_LOSS_COEFFICIENT,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_MAX_POWER_KW,
    CONF_MAX_SOC_PCT,
    CONF_MIN_CHARGE_KW,
    CONF_MIN_MODULATION_PCT,
    CONF_MIN_SOC_PCT,
    CONF_PEAK_POWER_KWP,
    CONF_PHASES,
    CONF_PLUG_STATE_ENTITY,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_SAFE_DEFAULT_VERIFY_ENTITY,
    CONF_SAFE_DEFAULT_VERIFY_EXPECTED,
    CONF_SAFE_DEFAULT_VERIFY_TIMEOUT,
    CONF_SOC_ENTITY,
    CONF_SOUTH_FACING,
    CONF_STANDBY_LOSS_W,
    CONF_THERMAL_MASS,
    CONF_TIER,
    CONF_TILT_DEG,
    CONF_U_VALUE,
    CONF_VENDOR_MODEL,
    CONF_VOLUME_LITERS,
    CONF_WINDOW_AREA_M2,
    DEFAULT_CONTROL_CLASS,
    DEVICE_PRO_SUPPORT,
    FORECAST_ADAPTERS,
    ConfigTier,
    ControlClassHA,
    DeviceType,
)

# Insulation class choices
INSULATION_CLASSES = ["good", "medium", "poor"]


def _number(min_val: float, max_val: float, step: float = 0.1) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=min_val, max=max_val, step=step, mode=NumberSelectorMode.BOX))


def _entity(domain: str | None = None) -> EntitySelector:
    if domain:
        return EntitySelector(EntitySelectorConfig(domain=domain))
    return EntitySelector(EntitySelectorConfig())


def _safe_default_schema(tier: str) -> dict:
    """Common safe_default fields required for all device types."""
    control_class_options = [c.value for c in ControlClassHA]
    schema: dict = {
        vol.Optional(CONF_CONTROL_CLASS, default=DEFAULT_CONTROL_CLASS.value): SelectSelector(
            SelectSelectorConfig(options=control_class_options, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(CONF_SAFE_DEFAULT_SCRIPT): TextSelector(TextSelectorConfig(type="text")),
    }
    if tier != ConfigTier.BEGINNER:
        schema[vol.Optional(CONF_SAFE_DEFAULT_VERIFY_ENTITY)] = _entity()
        schema[vol.Optional(CONF_SAFE_DEFAULT_VERIFY_EXPECTED)] = TextSelector(TextSelectorConfig(type="text"))
        schema[vol.Optional(CONF_SAFE_DEFAULT_VERIFY_TIMEOUT, default=300)] = _number(10, 3600, 10)
    return schema


def _build_room_schema(tier: str) -> vol.Schema:
    """Build schema for Room configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_FLOOR_AREA_M2): _number(1, 500, 1),
        vol.Required(CONF_INSULATION_CLASS, default="medium"): SelectSelector(
            SelectSelectorConfig(options=INSULATION_CLASSES, mode=SelectSelectorMode.DROPDOWN)
        ),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_THERMAL_MASS)] = _number(0.1, 100, 0.1)
        fields[vol.Optional(CONF_U_VALUE)] = _number(0.1, 10, 0.1)
        fields[vol.Optional(CONF_WINDOW_AREA_M2)] = _number(0, 100, 0.5)
        fields[vol.Optional(CONF_SOUTH_FACING, default=False)] = bool
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_thermostat_load_schema(tier: str) -> vol.Schema:
    """Build schema for ThermostatLoad configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_MAX_POWER_KW): _number(0.1, 50, 0.1),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_HYSTERESIS_K, default=0.5)] = _number(0.1, 5, 0.1)
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_heat_pump_schema(tier: str) -> vol.Schema:
    """Build schema for HeatPump configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_MAX_POWER_KW): _number(0.5, 30, 0.1),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_VENDOR_MODEL)] = TextSelector(TextSelectorConfig(type="text"))
        fields[vol.Optional(CONF_MIN_MODULATION_PCT, default=0)] = _number(0, 100, 1)
    if tier == ConfigTier.PRO:
        fields[vol.Optional(CONF_DEFROST_LOCKOUT_MIN, default=0)] = _number(0, 60, 1)
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_water_heater_schema(tier: str) -> vol.Schema:
    """Build schema for WaterHeater configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_VOLUME_LITERS): _number(10, 1000, 10),
        vol.Required(CONF_MAX_POWER_KW): _number(0.5, 20, 0.1),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_STANDBY_LOSS_W, default=50)] = _number(0, 500, 5)
        fields[vol.Optional(CONF_INSULATION_CLASS, default="medium")] = SelectSelector(
            SelectSelectorConfig(options=INSULATION_CLASSES, mode=SelectSelectorMode.DROPDOWN)
        )
    if tier == ConfigTier.PRO:
        fields[vol.Optional(CONF_LOSS_COEFFICIENT)] = _number(0.1, 20, 0.1)
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_battery_schema(tier: str) -> vol.Schema:
    """Build schema for Battery configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_CAPACITY_KWH): _number(0.5, 200, 0.5),
        vol.Required(CONF_MAX_CHARGE_KW): _number(0.1, 100, 0.1),
        vol.Required(CONF_MAX_DISCHARGE_KW): _number(0.1, 100, 0.1),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_CHARGE_EFFICIENCY, default=0.95)] = _number(0.5, 1.0, 0.01)
        fields[vol.Optional(CONF_DISCHARGE_EFFICIENCY, default=0.95)] = _number(0.5, 1.0, 0.01)
    if tier == ConfigTier.PRO:
        fields[vol.Optional(CONF_MIN_SOC_PCT, default=10)] = _number(0, 100, 1)
        fields[vol.Optional(CONF_MAX_SOC_PCT, default=100)] = _number(0, 100, 1)
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_pv_forecast_schema(tier: str) -> vol.Schema:
    """Build schema for PVForecast configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_PEAK_POWER_KWP): _number(0.1, 200, 0.1),
        vol.Required(CONF_FORECAST_ADAPTER, default="solcast"): SelectSelector(
            SelectSelectorConfig(options=FORECAST_ADAPTERS, mode=SelectSelectorMode.DROPDOWN)
        ),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_AZIMUTH_DEG, default=180)] = _number(0, 359, 1)
        fields[vol.Optional(CONF_TILT_DEG, default=30)] = _number(0, 90, 1)
    if tier == ConfigTier.PRO:
        fields[vol.Optional(CONF_FORECAST_ENTITY)] = _entity("sensor")
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


def _build_ev_charger_schema(tier: str) -> vol.Schema:
    """Build schema for EVCharger configuration."""
    fields: dict = {
        vol.Required(CONF_DEVICE_NAME): TextSelector(TextSelectorConfig(type="text")),
        vol.Required(CONF_MAX_CHARGE_KW): _number(1, 350, 0.1),
    }
    if tier in (ConfigTier.ADVANCED, ConfigTier.PRO):
        fields[vol.Optional(CONF_MIN_CHARGE_KW, default=0)] = _number(0, 50, 0.1)
        fields[vol.Optional(CONF_PHASES, default=3)] = _number(1, 3, 1)
    if tier == ConfigTier.PRO:
        fields[vol.Optional(CONF_PLUG_STATE_ENTITY)] = _entity("binary_sensor")
        fields[vol.Optional(CONF_SOC_ENTITY)] = _entity("sensor")
        fields[vol.Optional(CONF_BATTERY_CAPACITY_KWH)] = _number(10, 200, 1)
    fields.update(_safe_default_schema(tier))
    return vol.Schema(fields)


# Registry: device_type -> schema builder
DEVICE_SCHEMA_BUILDERS: dict[str, Any] = {
    DeviceType.ROOM: _build_room_schema,
    DeviceType.THERMOSTAT_LOAD: _build_thermostat_load_schema,
    DeviceType.HEAT_PUMP: _build_heat_pump_schema,
    DeviceType.WATER_HEATER: _build_water_heater_schema,
    DeviceType.BATTERY: _build_battery_schema,
    DeviceType.PV_FORECAST: _build_pv_forecast_schema,
    DeviceType.EV_CHARGER: _build_ev_charger_schema,
}


class HemmDeviceFlowMixin:
    """Mixin providing device add steps for the options flow.

    Provides async_step_select_device and async_step_configure_device.
    """

    _device_type: str
    _device_tier: str

    async def async_step_select_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step: select device type and tier."""
        if user_input is not None:
            self._device_type = user_input[CONF_DEVICE_TYPE]
            self._device_tier = user_input.get(CONF_TIER, ConfigTier.BEGINNER)
            # Validate tier is allowed for device type
            if self._device_tier == ConfigTier.PRO and self._device_type not in DEVICE_PRO_SUPPORT:
                self._device_tier = ConfigTier.ADVANCED
            return await self.async_step_configure_device()

        device_options = [dt.value for dt in DeviceType]
        tier_options = [t.value for t in ConfigTier]

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_TYPE): SelectSelector(
                    SelectSelectorConfig(options=device_options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_TIER, default=ConfigTier.BEGINNER): SelectSelector(
                    SelectSelectorConfig(options=tier_options, mode=SelectSelectorMode.DROPDOWN)
                ),
            }
        )

        return self.async_show_form(step_id="select_device", data_schema=schema)

    async def async_step_configure_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step: configure device-specific parameters."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate safe_default_script is present
            if not user_input.get(CONF_SAFE_DEFAULT_SCRIPT):
                errors[CONF_SAFE_DEFAULT_SCRIPT] = "safe_default_required"
            else:
                # Build device entry and add it to the config entry data
                device_entry = {
                    "id": str(uuid.uuid4()),
                    CONF_DEVICE_TYPE: self._device_type,
                    CONF_TIER: self._device_tier,
                    **user_input,
                }

                # Store in config entry data
                current_devices = list(self.config_entry.data.get("devices", []))
                current_devices.append(device_entry)

                # Update config entry data with the new device list
                new_data = {**self.config_entry.data, "devices": current_devices}
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)

                return self.async_create_entry(title="", data=self.config_entry.options)

        # Build schema for the selected device type and tier
        schema_builder = DEVICE_SCHEMA_BUILDERS[self._device_type]
        schema = schema_builder(self._device_tier)

        return self.async_show_form(
            step_id="configure_device",
            data_schema=schema,
            errors=errors,
        )
