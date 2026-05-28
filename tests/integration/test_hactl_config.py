"""Config entry and flow tests via hactl binary.

Tests the full config flow lifecycle: start flow, submit data, create entry,
options flow for device management, reload, and abort on duplicate.
"""

from __future__ import annotations

import pytest

from .hactl import Hactl, HactlError

# Standard HEMM config flow data
HEMM_FLOW_DATA = {
    "name": "HEMM",
    "horizon_hours": 24,
    "max_iterations": 50,
    "price_adapter": "template",
    "solver_backend": "milp_central",
}


def _get_hemm_entry_id(hactl: Hactl) -> str | None:
    """Helper: get the hemm config entry ID, or None if not set up."""
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    return hemm_entries[0]["entry_id"] if hemm_entries else None


def _ensure_hemm_entry(hactl: Hactl) -> str:
    """Helper: ensure HEMM is set up and return the entry ID."""
    entry_id = _get_hemm_entry_id(hactl)
    if entry_id:
        return entry_id

    # Set up via flow
    result = hactl.config_flow_start("hemm")
    flow_id = result.json_data["flow_id"]
    hactl.config_flow_step(flow_id, HEMM_FLOW_DATA)

    entry_id = _get_hemm_entry_id(hactl)
    assert entry_id, "Failed to create HEMM entry"
    return entry_id


@pytest.mark.container
class TestConfigFlowStart:
    """Tests for starting the HEMM config flow."""

    def test_flow_start_returns_form(self, hactl: Hactl) -> None:
        """Starting a config flow returns a form with step_id='user'."""
        result = hactl.config_flow_start("hemm")
        assert result.success
        data = result.json_data
        assert data is not None
        assert data.get("type") == "form"
        assert data.get("step_id") == "user"
        assert "flow_id" in data

    def test_flow_start_has_data_schema(self, hactl: Hactl) -> None:
        """Config flow form includes a data schema describing expected fields."""
        result = hactl.config_flow_start("hemm")
        data = result.json_data
        # The schema should describe the fields we need to fill
        assert "data_schema" in data or "schema" in data or "description_placeholders" in data

    def test_flow_inspect_shows_current_step(self, hactl: Hactl) -> None:
        """hactl config flow-inspect shows the current step details."""
        result = hactl.config_flow_start("hemm")
        flow_id = result.json_data["flow_id"]
        inspect_result = hactl.config_flow_inspect(flow_id)
        assert inspect_result.success


@pytest.mark.container
class TestConfigFlowComplete:
    """Tests for completing the HEMM config flow."""

    @pytest.mark.req("007:FR-001")
    def test_flow_creates_entry(self, hactl: Hactl) -> None:
        """Submitting valid data creates a config entry."""
        result = hactl.config_flow_start("hemm")
        flow_id = result.json_data["flow_id"]

        result = hactl.config_flow_step(flow_id, HEMM_FLOW_DATA)
        assert result.success
        data = result.json_data
        # create_entry or abort (if already exists)
        assert data.get("type") in ("create_entry", "abort")
        if data.get("type") == "create_entry":
            assert data.get("title") == "HEMM"

    @pytest.mark.req("007:FR-002")
    def test_flow_abort_already_configured(self, hactl: Hactl) -> None:
        """Second config flow for hemm aborts with 'already_configured'."""
        # Ensure first entry exists
        _ensure_hemm_entry(hactl)

        # Try to create a second
        result = hactl.config_flow_start("hemm")
        flow_id = result.json_data["flow_id"]

        result = hactl.config_flow_step(
            flow_id,
            {
                "name": "Another HEMM",
                "horizon_hours": 48,
                "max_iterations": 100,
                "price_adapter": "solcast",
                "solver_backend": "distributed",
            },
        )
        assert result.success
        assert result.json_data.get("type") == "abort"
        assert result.json_data.get("reason") == "already_configured"


@pytest.mark.container
class TestConfigEntries:
    """Tests for config entry state inspection."""

    def test_config_entries_lists_hemm(self, hactl: Hactl) -> None:
        """hactl config entries shows hemm as loaded."""
        _ensure_hemm_entry(hactl)
        result = hactl.config_entries()
        assert result.success

        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
        assert len(hemm_entries) == 1
        assert hemm_entries[0]["state"] == "loaded"

    def test_config_entry_has_expected_data(self, hactl: Hactl) -> None:
        """Config entry contains the data submitted during flow."""
        _ensure_hemm_entry(hactl)
        result = hactl.config_entries()
        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entry = next(e for e in entries if e.get("domain") == "hemm")
        # Entry should reference hemm domain and have a title
        assert hemm_entry["domain"] == "hemm"
        assert hemm_entry.get("title")


@pytest.mark.container
class TestOptionsFlow:
    """Tests for the options flow (device management)."""

    def test_options_flow_starts(self, hactl: Hactl) -> None:
        """Options flow can be started for the hemm entry."""
        entry_id = _ensure_hemm_entry(hactl)
        result = hactl.config_options(entry_id)
        assert result.success
        assert result.json_data is not None
        assert "flow_id" in result.json_data
        assert result.json_data.get("type") == "form"

    def test_options_flow_add_device_action(self, hactl: Hactl) -> None:
        """Options flow supports 'add_device' action."""
        entry_id = _ensure_hemm_entry(hactl)
        result = hactl.config_options(entry_id)
        flow_id = result.json_data["flow_id"]

        result = hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
        assert result.success
        assert result.json_data.get("step_id") == "select_device"

    @pytest.mark.req("007:FR-003")
    def test_options_flow_add_battery(self, hactl: Hactl) -> None:
        """Full device add flow for battery type."""
        entry_id = _ensure_hemm_entry(hactl)
        result = hactl.config_options(entry_id)
        flow_id = result.json_data["flow_id"]

        # Step 1: action
        result = hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
        assert result.json_data.get("step_id") == "select_device"

        # Step 2: select type
        result = hactl.config_flow_step(flow_id, {"device_type": "battery", "tier": "beginner"}, options=True)
        assert result.json_data.get("step_id") == "configure_device"

        # Step 3: configure
        result = hactl.config_flow_step(
            flow_id,
            {
                "device_name": "Test Battery",
                "capacity_kwh": 10.0,
                "max_charge_kw": 5.0,
                "max_discharge_kw": 5.0,
                "safe_default_script": "script.hemm_battery_safe",
            },
            options=True,
        )
        assert result.success
        assert result.json_data.get("type") == "create_entry"

    def test_options_flow_safe_default_required(self, hactl: Hactl) -> None:
        """Device config without safe_default_script is rejected."""
        entry_id = _ensure_hemm_entry(hactl)
        result = hactl.config_options(entry_id)
        flow_id = result.json_data["flow_id"]

        # action → select_device → configure without safe_default
        hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
        hactl.config_flow_step(flow_id, {"device_type": "thermostat_load", "tier": "beginner"}, options=True)

        try:
            result = hactl.config_flow_step(
                flow_id,
                {
                    "device_name": "No Safe Default",
                    "max_power_kw": 2.0,
                    # Missing safe_default_script
                },
                options=True,
            )
            # If we get here, should get an error form (not create_entry)
            assert result.json_data.get("type") != "create_entry"
        except HactlError as e:
            # 400 Bad Request means HA schema validation rejected it — expected
            assert "400" in str(e)


@pytest.mark.container
class TestConfigReload:
    """Tests for config entry reload."""

    @pytest.mark.req("007:FR-007")
    def test_reload_keeps_entry_loaded(self, hactl: Hactl) -> None:
        """Reloading the entry keeps it in 'loaded' state."""
        entry_id = _ensure_hemm_entry(hactl)

        # Reload via service call
        hactl.svc_call("homeassistant.reload_config_entry", {"entry_id": entry_id})

        # Verify still loaded
        result = hactl.config_entries()
        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entry = next(e for e in entries if e.get("domain") == "hemm")
        assert hemm_entry["state"] == "loaded"

    def test_config_check_passes(self, hactl: Hactl) -> None:
        """HA config check passes with hemm installed."""
        result = hactl.config_check()
        assert result.success
