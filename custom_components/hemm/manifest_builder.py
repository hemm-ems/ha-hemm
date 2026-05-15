"""Manifest builder — converts HA device config entries to hemm core manifests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hemm_core.manifest.types import DeviceManifest

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
    CONF_LOAD_PROFILE_ENTITY,
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
    CONF_SINK_TYPE,
    CONF_SOC_ENTITY,
    CONF_SOURCE_KIND,
    CONF_SOURCE_TYPE,
    CONF_SOUTH_FACING,
    CONF_STANDBY_LOSS_W,
    CONF_THERMAL_MASS,
    CONF_TILT_DEG,
    CONF_TYPICAL_DAILY_KWH,
    CONF_U_VALUE,
    CONF_VENDOR_MODEL,
    CONF_VOLUME_LITERS,
    CONF_WINDOW_AREA_M2,
    DeviceType,
)


def _build_safe_default(device: dict[str, Any]) -> Any:
    """Build a safe default Action from device config."""
    from hemm_core.manifest.types import Action, VerificationContract

    verify = None
    if device.get(CONF_SAFE_DEFAULT_VERIFY_ENTITY):
        verify = VerificationContract(
            entity=device[CONF_SAFE_DEFAULT_VERIFY_ENTITY],
            expected=device.get(CONF_SAFE_DEFAULT_VERIFY_EXPECTED, "== on"),
            within_seconds=device.get(CONF_SAFE_DEFAULT_VERIFY_TIMEOUT, 300),
        )
    return Action(
        script=device[CONF_SAFE_DEFAULT_SCRIPT],
        verify=verify,
    )


def build_manifest(device: dict[str, Any]) -> DeviceManifest:
    """Convert a single HA device config dict to a hemm core manifest.

    Args:
        device: Device config dict from config_entry.data["devices"].

    Returns:
        A typed manifest object (one of the 7 manifest types).

    Raises:
        ValueError: If device_type is unknown.
    """
    device_type = device[CONF_DEVICE_TYPE]
    device_id = device["id"]
    name = device.get(CONF_DEVICE_NAME, "Unknown")
    safe_default = _build_safe_default(device)
    control_class = device.get(CONF_CONTROL_CLASS)

    builders = {
        DeviceType.ROOM: _build_room,
        DeviceType.THERMOSTAT_LOAD: _build_thermostat_load,
        DeviceType.HEAT_PUMP: _build_heat_pump,
        DeviceType.WATER_HEATER: _build_water_heater,
        DeviceType.BATTERY: _build_battery,
        DeviceType.PV_FORECAST: _build_pv_forecast,
        DeviceType.EV_CHARGER: _build_ev_charger,
        DeviceType.PASSIVE_LOAD: _build_passive_load,
    }

    builder = builders.get(device_type)
    if builder is None:
        msg = f"Unknown device type: {device_type}"
        raise ValueError(msg)

    return builder(device_id, name, safe_default, device, control_class=control_class)


def build_all_manifests(devices: list[dict[str, Any]]) -> list[Any]:
    """Convert all HA device configs to hemm core manifests."""
    return [build_manifest(d) for d in devices]


def _build_room(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import RoomManifest

    return RoomManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        floor_area_m2=cfg[CONF_FLOOR_AREA_M2],
        insulation_class=cfg.get(CONF_INSULATION_CLASS),
        thermal_mass_kwh_per_k=cfg.get(CONF_THERMAL_MASS),
        u_value_w_per_m2k=cfg.get(CONF_U_VALUE),
        window_area_m2=cfg.get(CONF_WINDOW_AREA_M2),
        south_facing_windows=cfg.get(CONF_SOUTH_FACING, False),
    )


def _build_thermostat_load(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import ThermostatLoadManifest

    return ThermostatLoadManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        max_power_kw=cfg[CONF_MAX_POWER_KW],
        hysteresis_k=cfg.get(CONF_HYSTERESIS_K, 0.5),
    )


def _build_heat_pump(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import HeatPumpManifest

    return HeatPumpManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        max_power_kw=cfg[CONF_MAX_POWER_KW],
        vendor_model=cfg.get(CONF_VENDOR_MODEL),
        min_modulation_pct=cfg.get(CONF_MIN_MODULATION_PCT, 0),
        defrost_lockout_minutes=cfg.get(CONF_DEFROST_LOCKOUT_MIN, 0),
        source_type=cfg.get(CONF_SOURCE_TYPE, "air"),
        sink_type=cfg.get(CONF_SINK_TYPE, "water"),
    )


def _build_water_heater(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import WaterHeaterManifest

    return WaterHeaterManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        volume_liters=cfg[CONF_VOLUME_LITERS],
        max_power_kw=cfg[CONF_MAX_POWER_KW],
        standby_loss_w=cfg.get(CONF_STANDBY_LOSS_W, 50),
        insulation_class=cfg.get(CONF_INSULATION_CLASS),
        loss_coefficient_w_per_k=cfg.get(CONF_LOSS_COEFFICIENT),
    )


def _build_battery(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import BatteryManifest

    return BatteryManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        capacity_kwh=cfg[CONF_CAPACITY_KWH],
        max_charge_kw=cfg[CONF_MAX_CHARGE_KW],
        max_discharge_kw=cfg[CONF_MAX_DISCHARGE_KW],
        charge_efficiency=cfg.get(CONF_CHARGE_EFFICIENCY, 0.95),
        discharge_efficiency=cfg.get(CONF_DISCHARGE_EFFICIENCY, 0.95),
        min_soc_pct=cfg.get(CONF_MIN_SOC_PCT, 10),
        max_soc_pct=cfg.get(CONF_MAX_SOC_PCT, 100),
    )


def _build_pv_forecast(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import PVForecastManifest

    return PVForecastManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        peak_power_kwp=cfg[CONF_PEAK_POWER_KWP],
        source_kind=cfg.get(CONF_SOURCE_KIND, "pv"),
        azimuth_deg=cfg.get(CONF_AZIMUTH_DEG, 180),
        tilt_deg=cfg.get(CONF_TILT_DEG, 30),
        forecast_adapter=cfg.get(CONF_FORECAST_ADAPTER, "solcast"),
        forecast_entity=cfg.get(CONF_FORECAST_ENTITY),
    )


def _build_ev_charger(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import EVChargerManifest

    return EVChargerManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        max_charge_kw=cfg[CONF_MAX_CHARGE_KW],
        min_charge_kw=cfg.get(CONF_MIN_CHARGE_KW, 0),
        phases=cfg.get(CONF_PHASES, 3),
        plug_state_entity=cfg.get(CONF_PLUG_STATE_ENTITY),
        soc_entity=cfg.get(CONF_SOC_ENTITY),
        battery_capacity_kwh=cfg.get(CONF_BATTERY_CAPACITY_KWH),
    )


def _build_passive_load(
    device_id: str, name: str, safe_default: Any, cfg: dict[str, Any], *, control_class: str | None = None
) -> Any:
    from hemm_core.manifest.types import PassiveLoadManifest

    return PassiveLoadManifest(
        device_id=device_id,
        name=name,
        safe_default=safe_default,
        **({"control_class": control_class} if control_class else {}),
        typical_daily_kwh=cfg[CONF_TYPICAL_DAILY_KWH],
        load_profile_entity=cfg.get(CONF_LOAD_PROFILE_ENTITY),
    )
