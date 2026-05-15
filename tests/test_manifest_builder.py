"""Tests for the manifest builder — converts HA config to hemm core manifests."""

from __future__ import annotations

import pytest

from custom_components.hemm.const import (
    CONF_AZIMUTH_DEG,
    CONF_CAPACITY_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_FLOOR_AREA_M2,
    CONF_FORECAST_ADAPTER,
    CONF_HYSTERESIS_K,
    CONF_INSULATION_CLASS,
    CONF_MAX_CHARGE_KW,
    CONF_MAX_DISCHARGE_KW,
    CONF_MAX_POWER_KW,
    CONF_PEAK_POWER_KWP,
    CONF_PHASES,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_SAFE_DEFAULT_VERIFY_ENTITY,
    CONF_SAFE_DEFAULT_VERIFY_EXPECTED,
    CONF_SAFE_DEFAULT_VERIFY_TIMEOUT,
    CONF_TIER,
    CONF_VOLUME_LITERS,
    DeviceType,
)


def _make_device(device_type: str, **overrides) -> dict:
    """Create a minimal device config dict."""
    base = {
        "id": "test-device-001",
        CONF_DEVICE_TYPE: device_type,
        CONF_DEVICE_NAME: "Test Device",
        CONF_TIER: "beginner",
        CONF_SAFE_DEFAULT_SCRIPT: "script.test_safe_default",
    }
    base.update(overrides)
    return base


# These tests require hemm core to be importable. Skip if not available.
try:
    import hemm_core.manifest.types  # noqa: F401
except (ImportError, ModuleNotFoundError):
    pytest.skip("hemm core not importable", allow_module_level=True)


@pytest.mark.unit
class TestManifestBuilder:
    """Tests for manifest_builder module."""

    def test_build_room(self) -> None:
        """Test building a Room manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.ROOM,
            **{CONF_FLOOR_AREA_M2: 25.0, CONF_INSULATION_CLASS: "medium"},
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "room"
        assert manifest.floor_area_m2 == 25.0
        assert manifest.device_id == "test-device-001"
        assert manifest.safe_default.script == "script.test_safe_default"

    def test_build_thermostat_load(self) -> None:
        """Test building a ThermostatLoad manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.THERMOSTAT_LOAD,
            **{CONF_MAX_POWER_KW: 2.0, CONF_HYSTERESIS_K: 0.5},
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "thermostat_load"
        assert manifest.max_power_kw == 2.0

    def test_build_heat_pump(self) -> None:
        """Test building a HeatPump manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(DeviceType.HEAT_PUMP, **{CONF_MAX_POWER_KW: 5.0})
        manifest = build_manifest(device)
        assert manifest.type.value == "heat_pump"
        assert manifest.max_power_kw == 5.0

    def test_build_water_heater(self) -> None:
        """Test building a WaterHeater manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.WATER_HEATER,
            **{CONF_VOLUME_LITERS: 200.0, CONF_MAX_POWER_KW: 3.0},
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "water_heater"
        assert manifest.volume_liters == 200.0

    def test_build_battery(self) -> None:
        """Test building a Battery manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.BATTERY,
            **{
                CONF_CAPACITY_KWH: 10.0,
                CONF_MAX_CHARGE_KW: 5.0,
                CONF_MAX_DISCHARGE_KW: 5.0,
                CONF_CHARGE_EFFICIENCY: 0.9,
            },
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "battery"
        assert manifest.capacity_kwh == 10.0
        assert manifest.charge_efficiency == 0.9

    def test_build_pv_forecast(self) -> None:
        """Test building a PVForecast manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.PV_FORECAST,
            **{
                CONF_PEAK_POWER_KWP: 8.0,
                CONF_AZIMUTH_DEG: 180,
                CONF_FORECAST_ADAPTER: "solcast",
            },
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "pv_forecast"
        assert manifest.peak_power_kwp == 8.0

    def test_build_ev_charger(self) -> None:
        """Test building an EVCharger manifest."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.EV_CHARGER,
            **{CONF_MAX_CHARGE_KW: 11.0, CONF_PHASES: 3},
        )
        manifest = build_manifest(device)
        assert manifest.type.value == "ev_charger"
        assert manifest.max_charge_kw == 11.0

    def test_build_all_manifests(self) -> None:
        """Test building manifests for all device types."""
        from custom_components.hemm.manifest_builder import build_all_manifests

        devices = [
            _make_device(DeviceType.ROOM, id="d1", **{CONF_FLOOR_AREA_M2: 20.0}),
            _make_device(
                DeviceType.BATTERY,
                id="d2",
                **{
                    CONF_CAPACITY_KWH: 5.0,
                    CONF_MAX_CHARGE_KW: 3.0,
                    CONF_MAX_DISCHARGE_KW: 3.0,
                },
            ),
        ]
        manifests = build_all_manifests(devices)
        assert len(manifests) == 2
        assert manifests[0].type.value == "room"
        assert manifests[1].type.value == "battery"

    def test_unknown_device_type_raises(self) -> None:
        """Test that unknown device type raises ValueError."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device("unknown_type")
        with pytest.raises(ValueError, match="Unknown device type"):
            build_manifest(device)

    def test_safe_default_with_verification(self) -> None:
        """Test that verification contract is built when provided."""
        from custom_components.hemm.manifest_builder import build_manifest

        device = _make_device(
            DeviceType.ROOM,
            **{
                CONF_FLOOR_AREA_M2: 20.0,
                CONF_SAFE_DEFAULT_VERIFY_ENTITY: "sensor.temp",
                CONF_SAFE_DEFAULT_VERIFY_EXPECTED: ">= 18",
                CONF_SAFE_DEFAULT_VERIFY_TIMEOUT: 120,
            },
        )
        manifest = build_manifest(device)
        assert manifest.safe_default.verify is not None
        assert manifest.safe_default.verify.entity == "sensor.temp"
        assert manifest.safe_default.verify.expected == ">= 18"
        assert manifest.safe_default.verify.within_seconds == 120
