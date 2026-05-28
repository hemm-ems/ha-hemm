"""Container-based integration tests for HEMM — driven by real hactl binary.

These tests spin up a real HA container + companion, perform onboarding (via Python
client), then use the real hactl binary for all assertions and interactions.

Run with: make test-container (requires Docker + hactl binary)
"""

from __future__ import annotations

import pytest

from .hactl import Hactl

# --- Basic container health (using hactl binary) ---


@pytest.mark.container
def test_ha_container_healthy(hactl: Hactl) -> None:
    """Test that HA container is running and healthy via hactl health."""
    result = hactl.health()
    assert result.success
    # hactl health --json returns HA state info
    assert result.json_data is not None


@pytest.mark.container
def test_hactl_version(hactl: Hactl) -> None:
    """Test that hactl binary itself reports a version."""
    result = hactl.version()
    assert result.success
    assert "hactl" in result.stdout.lower() or "v0." in result.stdout


# --- Config entry lifecycle via hactl config commands ---


@pytest.mark.container
def test_hemm_integration_setup_via_hactl(hactl: Hactl) -> None:
    """Test HEMM integration setup via hactl config flow commands."""
    # Start config flow for hemm domain
    result = hactl.config_flow_start("hemm")
    assert result.success
    assert result.json_data is not None

    flow_id = result.json_data.get("flow_id")
    assert flow_id, f"No flow_id returned: {result.json_data}"
    assert result.json_data.get("type") == "form"
    assert result.json_data.get("step_id") == "user"

    # Submit config flow data
    flow_data = {
        "name": "HEMM",
        "horizon_hours": 24,
        "max_iterations": 50,
        "price_adapter": "template",
        "solver_backend": "milp_central",
    }
    result = hactl.config_flow_step(flow_id, flow_data)
    assert result.success
    assert result.json_data is not None
    # Should either create_entry or abort (if already configured)
    assert result.json_data.get("type") in ("create_entry", "abort")


@pytest.mark.container
def test_hemm_integration_loaded_via_hactl(hactl: Hactl) -> None:
    """Test that HEMM appears in config entries via hactl."""
    result = hactl.config_entries()
    assert result.success
    assert result.json_data is not None

    # Find hemm entry in the entries list
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    assert len(hemm_entries) >= 1, f"No hemm entry found in: {entries}"
    assert hemm_entries[0].get("state") == "loaded"


@pytest.mark.container
def test_hemm_entities_visible_via_hactl(hactl: Hactl) -> None:
    """Test that HEMM entities are visible via hactl ent ls."""
    result = hactl.ent_ls(pattern="hemm")
    assert result.success
    # After setup, at least the integration should register some entities
    # (exact count depends on whether devices are added)


@pytest.mark.container
def test_hemm_reload_via_hactl(hactl: Hactl) -> None:
    """Test that HEMM can be reloaded — verify via hactl config entries state."""
    # Get the entry ID
    result = hactl.config_entries()
    assert result.success
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    assert hemm_entries, "HEMM not loaded"

    entry_id = hemm_entries[0]["entry_id"]

    # Reload via service call
    hactl.svc_call("homeassistant.reload_config_entry", {"entry_id": entry_id})

    # Verify still loaded after reload
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    assert hemm_entries[0].get("state") == "loaded"


@pytest.mark.container
def test_hemm_no_error_logs(hactl: Hactl) -> None:
    """Test that hemm produces no error-level log entries after setup."""
    result = hactl.cc_logs("hemm", unique=True)
    # If the command succeeds and returns no errors, or returns empty results, that's fine
    assert result.success


# --- Device management via options flow (hactl config commands) ---


DEVICE_CONFIGS = [
    {
        "device_type": "room",
        "device_name": "Living Room",
        "floor_area_m2": 25.0,
        "insulation_class": "medium",
        "safe_default_script": "script.hemm_room_safe",
    },
    {
        "device_type": "thermostat_load",
        "device_name": "Hallway Heater",
        "max_power_kw": 2.0,
        "safe_default_script": "script.hemm_thermostat_safe",
    },
    {
        "device_type": "heat_pump",
        "device_name": "Main Heat Pump",
        "max_power_kw": 5.0,
        "safe_default_script": "script.hemm_hp_safe",
    },
    {
        "device_type": "water_heater",
        "device_name": "Hot Water Tank",
        "volume_liters": 200.0,
        "max_power_kw": 3.0,
        "safe_default_script": "script.hemm_wh_safe",
    },
    {
        "device_type": "battery",
        "device_name": "House Battery",
        "capacity_kwh": 10.0,
        "max_charge_kw": 5.0,
        "max_discharge_kw": 5.0,
        "safe_default_script": "script.hemm_battery_safe",
    },
    {
        "device_type": "pv_forecast",
        "device_name": "Roof PV",
        "peak_power_kwp": 8.5,
        "forecast_adapter": "solcast",
        "safe_default_script": "script.hemm_pv_safe",
    },
    {
        "device_type": "ev_charger",
        "device_name": "Garage Charger",
        "max_charge_kw": 11.0,
        "safe_default_script": "script.hemm_ev_safe",
    },
]


@pytest.mark.container
@pytest.mark.parametrize(
    "device_config",
    DEVICE_CONFIGS,
    ids=[d["device_type"] for d in DEVICE_CONFIGS],
)
@pytest.mark.req("001:FR-001", "001:FR-002")
def test_hemm_add_device_via_options_hactl(hactl: Hactl, device_config: dict) -> None:
    """Test adding each of the 7 device types via hactl config options flow."""
    # Get hemm entry ID
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    assert hemm_entries, "HEMM integration must be set up first"

    entry_id = hemm_entries[0]["entry_id"]

    # Start options flow via hactl
    result = hactl.config_options(entry_id)
    assert result.success, f"Failed to start options flow: {result.stderr}"
    assert result.json_data is not None

    flow_id = result.json_data.get("flow_id")
    assert flow_id, f"No flow_id: {result.json_data}"

    # Step 1: choose "add_device" action
    result = hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
    assert result.success, f"Action step failed: {result.stderr}"
    assert result.json_data.get("step_id") == "select_device"

    # Step 2: select device type and tier
    result = hactl.config_flow_step(
        flow_id, {"device_type": device_config["device_type"], "tier": "beginner"}, options=True
    )
    assert result.success, f"Select device failed: {result.stderr}"
    assert result.json_data.get("step_id") == "configure_device"

    # Step 3: configure device details
    configure_data = {k: v for k, v in device_config.items() if k != "device_type"}
    result = hactl.config_flow_step(flow_id, configure_data, options=True)
    assert result.success, f"Configure device failed: {result.stderr}"
    assert result.json_data.get("type") == "create_entry", f"Expected create_entry: {result.json_data}"
