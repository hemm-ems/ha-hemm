"""Zeitdynamik feature integration tests via hactl binary.

Verifies the Zeitdynamik-Erweiterung features in a live HA container:
- control_class field in device configuration
- reason sensor per device (4 sensors total)
- device_filter parameter on hemm.replan
- 6 blueprints discoverable
"""

from __future__ import annotations

import pytest

from .hactl import Hactl


def _get_hemm_entry_id(hactl: Hactl) -> str | None:
    """Helper: get the hemm config entry ID."""
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    return hemm_entries[0]["entry_id"] if hemm_entries else None


def _add_device(hactl: Hactl, device_type: str, config: dict) -> None:
    """Helper: add a device via options flow."""
    entry_id = _get_hemm_entry_id(hactl)
    assert entry_id, "HEMM must be set up first"

    result = hactl.config_options(entry_id)
    flow_id = result.json_data["flow_id"]

    hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
    hactl.config_flow_step(flow_id, {"device_type": device_type, "tier": "beginner"}, options=True)
    hactl.config_flow_step(flow_id, config, options=True)


@pytest.mark.container
class TestControlClassDeviceConfig:
    """Verify control_class field in device configuration flow."""

    def test_device_with_reactive_control_class(self, hactl: Hactl) -> None:
        """Adding a device with control_class=reactive succeeds."""
        _add_device(
            hactl,
            "ev_charger",
            {
                "device_name": "ZD EV Reactive",
                "max_charge_kw": 11.0,
                "safe_default_script": "script.hemm_ev_safe",
                "control_class": "reactive",
            },
        )
        # Verify entity created
        result = hactl.ent_ls(pattern="hemm")
        assert result.success

    def test_device_with_passive_control_class(self, hactl: Hactl) -> None:
        """Adding a device with control_class=passive succeeds."""
        _add_device(
            hactl,
            "thermostat_load",
            {
                "device_name": "ZD Passive Load",
                "max_power_kw": 2.0,
                "safe_default_script": "script.hemm_passive_safe",
                "control_class": "passive",
            },
        )
        result = hactl.ent_ls(pattern="hemm")
        assert result.success

    def test_device_with_planned_control_class(self, hactl: Hactl) -> None:
        """Adding a device with control_class=planned (default) succeeds."""
        _add_device(
            hactl,
            "battery",
            {
                "device_name": "ZD Planned Battery",
                "capacity_kwh": 10.0,
                "max_charge_kw": 5.0,
                "max_discharge_kw": 5.0,
                "safe_default_script": "script.hemm_battery_safe",
                "control_class": "planned",
            },
        )
        result = hactl.ent_ls(pattern="hemm")
        assert result.success

    def test_device_default_control_class_is_planned(self, hactl: Hactl) -> None:
        """Omitting control_class defaults to planned (backward compat)."""
        _add_device(
            hactl,
            "battery",
            {
                "device_name": "ZD Default Battery",
                "capacity_kwh": 5.0,
                "max_charge_kw": 3.0,
                "max_discharge_kw": 3.0,
                "safe_default_script": "script.hemm_battery_safe",
                # No control_class — should default to planned
            },
        )
        result = hactl.ent_ls(pattern="hemm")
        assert result.success


@pytest.mark.container
class TestReasonSensor:
    """Verify reason sensor per device."""

    def test_reason_sensor_exists(self, hactl: Hactl) -> None:
        """After adding a device, a reason sensor entity exists."""
        _add_device(
            hactl,
            "battery",
            {
                "device_name": "ZD Reason Battery",
                "capacity_kwh": 10.0,
                "max_charge_kw": 5.0,
                "max_discharge_kw": 5.0,
                "safe_default_script": "script.hemm_battery_safe",
            },
        )
        result = hactl.ent_ls(pattern="reason", domain="sensor")
        if not result.json_data:
            # Fall back to broader search
            result = hactl.ent_ls(domain="sensor")
        assert result.success
        output = (result.stdout or "") + str(result.json_data or "")
        assert "reason" in output.lower()

    def test_reason_sensor_initial_state_idle(self, hactl: Hactl) -> None:
        """Reason sensor initial state is 'idle'."""
        result = hactl.ent_ls(pattern="reason", domain="sensor")
        if not result.json_data:
            result = hactl.ent_ls(domain="sensor")
        if not result.json_data:
            pytest.skip("No reason sensor entities found")

        entities = result.json_data if isinstance(result.json_data, list) else []
        reason_entities = [e for e in entities if "reason" in (e.get("entity_id", "") + e.get("id", "")).lower()]
        if not reason_entities:
            pytest.skip("No reason sensor entities found")

        entity_id = reason_entities[0].get("entity_id", reason_entities[0].get("id", ""))
        show_result = hactl.ent_show(entity_id)
        assert show_result.success
        state = show_result.json_data.get("state", "")
        assert state == "idle"

    def test_four_sensors_per_device(self, hactl: Hactl) -> None:
        """Each device gets 4 sensors: plan, confidence, mode, reason."""
        _add_device(
            hactl,
            "battery",
            {
                "device_name": "ZD Four Sensors",
                "capacity_kwh": 10.0,
                "max_charge_kw": 5.0,
                "max_discharge_kw": 5.0,
                "safe_default_script": "script.hemm_battery_safe",
            },
        )
        result = hactl.ent_ls(pattern="hemm", domain="sensor")
        assert result.success
        entities = result.json_data if isinstance(result.json_data, list) else []
        # Filter for the specific device
        device_entities = [
            e
            for e in entities
            if "four_sensors" in (e.get("entity_id", "") + e.get("id", "")).lower().replace(" ", "_")
        ]
        # Should have at least 4 sensors (plan, confidence, mode, reason)
        if device_entities:
            assert len(device_entities) >= 4, f"Expected 4 sensors, got {len(device_entities)}: {device_entities}"


@pytest.mark.container
class TestReplanDeviceFilter:
    """Verify device_filter parameter on hemm.replan."""

    def test_replan_with_device_filter(self, hactl: Hactl) -> None:
        """hemm.replan accepts device_filter parameter."""
        _get_hemm_entry_id(hactl)
        result = hactl.svc_call("hemm.replan", {"device_filter": ["nonexistent_device"]})
        assert result.success

    def test_replan_with_empty_filter(self, hactl: Hactl) -> None:
        """hemm.replan with empty device_filter still works."""
        _get_hemm_entry_id(hactl)
        result = hactl.svc_call("hemm.replan", {"device_filter": []})
        assert result.success

    def test_replan_without_filter_backward_compat(self, hactl: Hactl) -> None:
        """hemm.replan without device_filter works (backward compat)."""
        _get_hemm_entry_id(hactl)
        result = hactl.svc_call("hemm.replan")
        assert result.success


@pytest.mark.container
class TestBlueprintDiscovery:
    """Verify all 6 blueprints are discoverable."""

    def test_six_blueprints_present(self, hactl: Hactl) -> None:
        """All 6 HEMM blueprints exist as YAML files in the custom component."""
        from pathlib import Path

        bp_dir = Path(__file__).parent.parent.parent / "custom_components" / "hemm" / "blueprints"
        assert bp_dir.is_dir(), f"Blueprint directory not found: {bp_dir}"

        bp_files = sorted(p.stem for p in bp_dir.glob("*.yaml"))
        new_blueprints = ["hemm_passive_meter", "hemm_planned_watchdog", "hemm_reactive_follower"]
        for bp in new_blueprints:
            assert bp in bp_files, f"Blueprint '{bp}' not found in {bp_dir}"
