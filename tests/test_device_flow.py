"""Tests for the HEMM device config flow (options flow with device steps)."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hemm.const import (
    CONF_CAPACITY_KWH,
    CONF_CONTROL_CLASS,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_FLOOR_AREA_M2,
    CONF_FORECAST_ADAPTER,
    CONF_INSULATION_CLASS,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_MAX_POWER_KW,
    CONF_PEAK_POWER_KWP,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_TIER,
    CONF_VOLUME_LITERS,
    ConfigTier,
    ControlClassHA,
    DeviceType,
)


@pytest.mark.unit
async def test_options_flow_shows_action_choice(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that the options flow shows action selection (settings vs add device)."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.unit
async def test_options_flow_add_device_shows_select(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that choosing 'add_device' action shows device selection step."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"action": "add_device"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_device"


@pytest.mark.unit
async def test_options_flow_settings(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that choosing 'settings' shows settings form."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"action": "settings"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"


@pytest.mark.unit
@pytest.mark.parametrize(
    "device_type",
    [dt.value for dt in DeviceType],
    ids=[dt.value for dt in DeviceType],
)
async def test_device_type_shows_configure_step(
    hass: HomeAssistant, init_integration: ConfigEntry, device_type: str
) -> None:
    """Test that selecting any device type shows the configure step."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: device_type, CONF_TIER: ConfigTier.BEGINNER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "configure_device"


@pytest.mark.unit
async def test_add_room_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a Room device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.ROOM, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Living Room",
            CONF_FLOOR_AREA_M2: 25.0,
            CONF_INSULATION_CLASS: "medium",
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_room_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Verify device was added to config entry data
    devices = init_integration.data.get("devices", [])
    assert len(devices) == 1
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.ROOM
    assert devices[0][CONF_DEVICE_NAME] == "Living Room"
    assert devices[0][CONF_FLOOR_AREA_M2] == 25.0


@pytest.mark.unit
async def test_add_thermostat_load_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a ThermostatLoad device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.THERMOSTAT_LOAD, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Hallway Heater",
            CONF_MAX_POWER_KW: 2.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_thermostat_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert len(devices) == 1
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.THERMOSTAT_LOAD


@pytest.mark.unit
async def test_add_heat_pump_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a HeatPump device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.HEAT_PUMP, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Main Heat Pump",
            CONF_MAX_POWER_KW: 5.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_hp_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.HEAT_PUMP


@pytest.mark.unit
async def test_add_water_heater_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a WaterHeater device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.WATER_HEATER, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Hot Water Tank",
            CONF_VOLUME_LITERS: 200.0,
            CONF_MAX_POWER_KW: 3.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_wh_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.WATER_HEATER


@pytest.mark.unit
async def test_add_battery_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a Battery device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.BATTERY, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "House Battery",
            CONF_CAPACITY_KWH: 10.0,
            CONF_MAX_CHARGE_KW: 5.0,
            CONF_MAX_DISCHARGE_KW: 5.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_battery_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.BATTERY


@pytest.mark.unit
async def test_add_pv_forecast_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a PVForecast device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.PV_FORECAST, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Roof PV",
            CONF_PEAK_POWER_KWP: 8.5,
            CONF_FORECAST_ADAPTER: "solcast",
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_pv_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.PV_FORECAST


@pytest.mark.unit
async def test_add_ev_charger_beginner(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding an EVCharger device in beginner mode."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.EV_CHARGER, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Garage Charger",
            CONF_MAX_CHARGE_KW: 11.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_ev_safe",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_DEVICE_TYPE] == DeviceType.EV_CHARGER


@pytest.mark.unit
async def test_safe_default_required(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that missing safe_default_script causes an error."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.ROOM, CONF_TIER: ConfigTier.BEGINNER},
    )
    # Submit with empty safe_default_script
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Test Room",
            CONF_FLOOR_AREA_M2: 20.0,
            CONF_INSULATION_CLASS: "good",
            CONF_SAFE_DEFAULT_SCRIPT: "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"].get(CONF_SAFE_DEFAULT_SCRIPT) == "safe_default_required"


@pytest.mark.unit
async def test_heat_pump_pro_mode(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a HeatPump device in pro mode with extra fields."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.HEAT_PUMP, CONF_TIER: ConfigTier.PRO},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Pro Heat Pump",
            CONF_MAX_POWER_KW: 8.0,
            "vendor_model": "Daikin Altherma 3",
            "min_modulation_pct": 30.0,
            "defrost_lockout_minutes": 10.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_hp_safe",
            "safe_default_verify_entity": "sensor.hp_status",
            "safe_default_verify_expected": "== off",
            "safe_default_verify_timeout": 300.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0]["vendor_model"] == "Daikin Altherma 3"
    assert devices[0]["defrost_lockout_minutes"] == 10.0
    assert devices[0][CONF_TIER] == ConfigTier.PRO


@pytest.mark.unit
async def test_device_configure_includes_control_class(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test that control_class field is available and stored in device config."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.BATTERY, CONF_TIER: ConfigTier.BEGINNER},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Battery with Class",
            CONF_CAPACITY_KWH: 10.0,
            CONF_MAX_CHARGE_KW: 5.0,
            CONF_MAX_DISCHARGE_KW: 5.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.battery_safe",
            CONF_CONTROL_CLASS: ControlClassHA.REACTIVE,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0][CONF_CONTROL_CLASS] == ControlClassHA.REACTIVE


@pytest.mark.unit
async def test_battery_pro_mode(hass: HomeAssistant, init_integration: ConfigEntry) -> None:
    """Test adding a Battery device in pro mode with SoC limits."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"action": "add_device"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE_TYPE: DeviceType.BATTERY, CONF_TIER: ConfigTier.PRO},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEVICE_NAME: "Pro Battery",
            CONF_CAPACITY_KWH: 15.0,
            CONF_MAX_CHARGE_KW: 7.0,
            CONF_MAX_DISCHARGE_KW: 7.0,
            "charge_efficiency": 0.92,
            "discharge_efficiency": 0.93,
            "min_soc_pct": 20.0,
            "max_soc_pct": 90.0,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_battery_safe",
            "safe_default_verify_entity": "sensor.battery_mode",
            "safe_default_verify_expected": "== standby",
            "safe_default_verify_timeout": 120.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    devices = init_integration.data.get("devices", [])
    assert devices[0]["min_soc_pct"] == 20.0
    assert devices[0]["max_soc_pct"] == 90.0
