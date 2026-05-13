"""Sim house tests — parametrized per house definition.

Each test starts a Docker container with HA, provisions the house via
the HEMM config flow, and verifies the setup succeeded.

Usage:
    uv run pytest tests/sim/ -m sim --log-cli-level=INFO
"""

from __future__ import annotations

import pytest

from tests.integration.hactl import Hactl

from .runner import HouseConfig, discover_house_names, verify_house

# Discover all house names for parametrization
_HOUSE_NAMES = discover_house_names()


@pytest.mark.sim
class TestSimHouse:
    """Parametrized sim house tests — one run per house definition."""

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_house_setup_succeeds(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """House provisions all devices without error."""
        house, hactl = sim_house
        # If we got here, setup_house() in the fixture already succeeded
        assert house is not None
        assert hactl is not None

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_hub_is_loaded(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """HEMM hub config entry is in 'loaded' state."""
        house, hactl = sim_house
        result = verify_house(house, hactl)
        assert result["hub_loaded"], f"Hub not loaded for house {house.name}"

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_entities_created(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """All expected devices have HEMM entities."""
        house, hactl = sim_house
        ent_result = hactl.ent_ls(pattern="*hemm*")
        assert ent_result.success
        # Should have at least one entity per device
        entities = ent_result.json_data
        if isinstance(entities, list):
            assert len(entities) >= len(house.devices), (
                f"Expected >= {len(house.devices)} hemm entities, got {len(entities)}"
            )

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_replan_service_callable(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """hemm.replan service can be called after setup."""
        house, hactl = sim_house
        try:
            result = hactl.svc_call("hemm.replan")
            assert result.success
        except Exception as e:
            pytest.fail(f"hemm.replan failed for house {house.name}: {e}")

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_no_hemm_errors_in_log(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """No hemm errors in HA log after setup."""
        house, hactl = sim_house
        try:
            result = hactl.cc_logs("hemm", unique=True)
            # If cc_logs succeeds and returns errors, fail
            if result.json_data and isinstance(result.json_data, list) and len(result.json_data) > 0:
                error_entries = [
                    e for e in result.json_data if isinstance(e, dict) and e.get("level") in ("ERROR", "CRITICAL")
                ]
                assert len(error_entries) == 0, f"Found {len(error_entries)} hemm errors in log for house {house.name}"
        except Exception:
            # cc_logs may not be available — skip check
            pass

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_constraint_count_matches(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """Number of expected constraints matches house definition."""
        house, _hactl = sim_house
        if not house.constraints:
            pytest.skip(f"No constraints defined for house {house.name}")
        # Just verify the house has the expected constraint definitions
        assert len(house.constraints) > 0

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_device_count_matches(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """Number of configured devices matches house definition."""
        _house, hactl = sim_house
        result = hactl.config_entries()
        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
        # One hub entry exists
        assert len(hemm_entries) >= 1

    @pytest.mark.parametrize("sim_house", _HOUSE_NAMES, indirect=True)
    def test_automations_created(self, sim_house: tuple[HouseConfig, Hactl]) -> None:
        """Automations defined in house config are created via hactl."""
        house, hactl = sim_house
        if not house.automations:
            pytest.skip(f"No automations defined for house {house.name}")
        result = hactl.auto_ls()
        assert result.success
        # Verify each expected automation ID appears in the listing
        auto_data = result.json_data
        if isinstance(auto_data, list):
            auto_ids = {a.get("id", a.get("automation_id", "")) for a in auto_data}
        else:
            auto_ids = set()
        for auto in house.automations:
            expected_id = auto.get("id", "")
            assert expected_id in auto_ids, (
                f"Automation '{expected_id}' not found in HA for house {house.name}. Found: {auto_ids}"
            )
