"""Container-based integration tests for HEMM.

These tests spin up a real HA container, perform onboarding, install the HEMM
integration via the config flow API, and verify it works end-to-end.

Run with: make test-container (requires Docker)
"""

from __future__ import annotations

import pytest

from .hactl_client import HactlClient


@pytest.mark.container
async def test_ha_container_healthy(ha_client: HactlClient) -> None:
    """Test that HA container is running and healthy."""
    result = await ha_client.get_health()
    assert result.status == 200
    assert result.data.get("message") == "API running."


@pytest.mark.container
async def test_ha_config_accessible(ha_client: HactlClient) -> None:
    """Test that HA config is accessible after onboarding."""
    result = await ha_client.get_config()
    assert result.status == 200
    assert "version" in result.data
    assert "components" in result.data


@pytest.mark.container
async def test_hemm_integration_setup(ha_client: HactlClient) -> None:
    """Test that the HEMM integration can be set up via config flow."""
    # Create HEMM config entry via flow
    result = await ha_client.create_config_entry(
        domain="hemm",
        data={
            "name": "HEMM",
            "horizon_hours": 24,
            "max_iterations": 50,
            "price_adapter": "template",
            "solver_backend": "milp_central",
        },
    )
    assert result.status == 200
    assert result.data.get("type") == "create_entry"
    assert result.data.get("title") == "HEMM"


@pytest.mark.container
async def test_hemm_integration_loaded(ha_client: HactlClient) -> None:
    """Test that the HEMM domain appears in loaded config entries."""
    # First set up the integration
    await ha_client.create_config_entry(
        domain="hemm",
        data={
            "name": "HEMM",
            "horizon_hours": 24,
            "max_iterations": 50,
            "price_adapter": "template",
            "solver_backend": "milp_central",
        },
    )

    # Verify it's in config entries
    entries = await ha_client.get_config_entries()
    assert entries.status == 200
    hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]
    assert len(hemm_entries) >= 1
    assert hemm_entries[0]["state"] == "loaded"


@pytest.mark.container
async def test_hemm_diagnostics_retrievable(ha_client: HactlClient) -> None:
    """Test that diagnostics endpoint is accessible for the HEMM entry."""
    # Set up integration
    flow_result = await ha_client.create_config_entry(
        domain="hemm",
        data={
            "name": "HEMM",
            "horizon_hours": 24,
            "max_iterations": 50,
            "price_adapter": "template",
            "solver_backend": "milp_central",
        },
    )

    # Get the entry ID from the flow result
    entry_id = flow_result.data.get("result", {}).get("entry_id")
    if not entry_id:
        # Fetch from config entries
        entries = await ha_client.get_config_entries()
        hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]
        assert hemm_entries, "HEMM integration not found"
        entry_id = hemm_entries[0]["entry_id"]

    # Get diagnostics
    diag = await ha_client.get_diagnostics(entry_id)
    assert diag.status == 200
    assert "tested_ha_version" in diag.data.get("data", {})


@pytest.mark.container
async def test_hemm_reload(ha_client: HactlClient) -> None:
    """Test that the HEMM integration can be reloaded."""
    # Ensure integration exists
    entries = await ha_client.get_config_entries()
    hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]

    if not hemm_entries:
        await ha_client.create_config_entry(
            domain="hemm",
            data={
                "name": "HEMM",
                "horizon_hours": 24,
                "max_iterations": 50,
                "price_adapter": "template",
                "solver_backend": "milp_central",
            },
        )
        entries = await ha_client.get_config_entries()
        hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]

    entry_id = hemm_entries[0]["entry_id"]
    result = await ha_client.reload_integration(entry_id)
    # Reload should succeed (2xx)
    assert result.status in (200, 201, 204)


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
async def test_hemm_add_device_via_options(ha_client: HactlClient, device_config: dict) -> None:
    """Test adding each of the 7 device types via the options flow."""
    # Ensure integration exists
    entries = await ha_client.get_config_entries()
    hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]

    if not hemm_entries:
        await ha_client.create_config_entry(
            domain="hemm",
            data={
                "name": "HEMM",
                "horizon_hours": 24,
                "max_iterations": 50,
                "price_adapter": "template",
                "solver_backend": "milp_central",
            },
        )
        entries = await ha_client.get_config_entries()
        hemm_entries = [e for e in entries.data["entries"] if e.get("domain") == "hemm"]

    entry_id = hemm_entries[0]["entry_id"]

    # Start options flow
    result = await ha_client.start_options_flow(entry_id)
    assert result.status == 200, f"Failed to start options flow: {result.data}"
    flow_id = result.data.get("flow_id")
    assert flow_id, f"No flow_id in response: {result.data}"

    # Step 1: choose "add_device" action
    result = await ha_client.configure_options_flow(flow_id, {"action": "add_device"})
    assert result.status == 200, f"Failed action step: {result.data}"
    assert result.data.get("step_id") == "select_device"

    # Step 2: select device type and tier
    result = await ha_client.configure_options_flow(
        flow_id, {"device_type": device_config["device_type"], "tier": "beginner"}
    )
    assert result.status == 200, f"Failed select_device step: {result.data}"
    assert result.data.get("step_id") == "configure_device"

    # Step 3: configure device (exclude device_type — it was sent in select_device)
    configure_data = {k: v for k, v in device_config.items() if k != "device_type"}
    result = await ha_client.configure_options_flow(flow_id, configure_data)
    assert result.status == 200, f"Failed configure_device step: {result.data}"
    assert result.data.get("type") == "create_entry", f"Expected create_entry, got: {result.data}"
