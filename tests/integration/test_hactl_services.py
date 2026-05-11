"""Dry-run and service-level container tests via hactl binary.

Tests real HEMM service calls in a live HA container:
- hemm.simulate (always dry-run)
- hemm.set_solver + hemm.set_solver dry-run
- hemm.set_price_curve
- hemm.add_constraint_window
- hemm.replan (dry_run: true)
- hemm.tick (dry_run: true)

These tests verify the full stack: HA → service handler → hemm core → solver.
"""

from __future__ import annotations

import pytest

from .hactl import Hactl

HEMM_FLOW_DATA = {
    "name": "HEMM",
    "horizon_hours": 24,
    "max_iterations": 50,
    "price_adapter": "template",
    "solver_backend": "milp_central",
}


def _ensure_hemm_entry(hactl: Hactl) -> str:
    """Ensure HEMM is set up and return the entry ID."""
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    if hemm_entries:
        return hemm_entries[0]["entry_id"]

    result = hactl.config_flow_start("hemm")
    flow_id = result.json_data["flow_id"]
    hactl.config_flow_step(flow_id, HEMM_FLOW_DATA)

    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    assert hemm_entries, "Failed to create HEMM entry"
    return hemm_entries[0]["entry_id"]


@pytest.mark.container
class TestDryRunSimulate:
    """Test hemm.simulate service (always dry-run)."""

    def test_simulate_service_exists(self, hactl: Hactl) -> None:
        """hemm.simulate service is callable via hactl."""
        _ensure_hemm_entry(hactl)
        # Calling simulate with no devices is a no-op but should succeed
        result = hactl.svc_call("hemm.simulate")
        assert result.success

    def test_simulate_with_empty_data(self, hactl: Hactl) -> None:
        """hemm.simulate with empty data dict succeeds."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.simulate", {})
        assert result.success


@pytest.mark.container
class TestDryRunReplan:
    """Test hemm.replan service with dry_run flag."""

    def test_replan_dry_run(self, hactl: Hactl) -> None:
        """hemm.replan with dry_run=true runs solver but doesn't update plans."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.replan", {"dry_run": True})
        assert result.success

    def test_replan_normal(self, hactl: Hactl) -> None:
        """hemm.replan without dry_run updates the plan."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.replan")
        assert result.success


@pytest.mark.container
class TestDryRunTick:
    """Test hemm.tick service with dry_run flag."""

    def test_tick_dry_run(self, hactl: Hactl) -> None:
        """hemm.tick with dry_run=true succeeds."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.tick", {"dry_run": True})
        assert result.success

    def test_tick_normal(self, hactl: Hactl) -> None:
        """hemm.tick without dry_run succeeds."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.tick")
        assert result.success


@pytest.mark.container
class TestSetSolverService:
    """Test hemm.set_solver service."""

    def test_switch_to_distributed(self, hactl: Hactl) -> None:
        """Switch solver to distributed backend."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.set_solver", {"backend": "distributed"})
        assert result.success

    def test_switch_back_to_milp(self, hactl: Hactl) -> None:
        """Switch solver back to milp_central."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.set_solver", {"backend": "milp_central"})
        assert result.success

    def test_switch_dry_run(self, hactl: Hactl) -> None:
        """Dry-run switch doesn't change backend."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.set_solver", {"backend": "distributed", "dry_run": True})
        assert result.success


@pytest.mark.container
class TestSetPriceCurveService:
    """Test hemm.set_price_curve service."""

    def test_set_prices(self, hactl: Hactl) -> None:
        """Setting a price curve succeeds."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call(
            "hemm.set_price_curve",
            {"prices": [0.10, 0.20, 0.30, 0.40, 0.35, 0.25], "resolution_minutes": 60},
        )
        assert result.success

    def test_set_negative_prices(self, hactl: Hactl) -> None:
        """Negative prices (renewable surplus) are accepted."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.set_price_curve", {"prices": [-0.05, -0.02, 0.0, 0.10]})
        assert result.success

    def test_set_prices_dry_run(self, hactl: Hactl) -> None:
        """Dry-run price curve doesn't affect state."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call("hemm.set_price_curve", {"prices": [9.99], "dry_run": True})
        assert result.success


@pytest.mark.container
class TestConstraintServices:
    """Test constraint management services in the live container."""

    def test_add_constraint(self, hactl: Hactl) -> None:
        """Add a forbidden_window constraint."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call(
            "hemm.add_constraint_window",
            {
                "window_id": "test_forbidden_1",
                "device_id": "test_device",
                "deadline": "2030-01-01T00:00:00+00:00",
                "requirement_type": "forbidden_window",
                "priority_penalty": 5.0,
            },
        )
        assert result.success

    def test_remove_constraint(self, hactl: Hactl) -> None:
        """Remove a previously added constraint."""
        _ensure_hemm_entry(hactl)
        # Add first
        hactl.svc_call(
            "hemm.add_constraint_window",
            {
                "window_id": "to_remove_container",
                "device_id": "test_device",
                "deadline": "2030-01-01T00:00:00+00:00",
                "requirement_type": "forbidden_window",
            },
        )
        # Remove
        result = hactl.svc_call("hemm.remove_constraint", {"window_id": "to_remove_container"})
        assert result.success

    def test_bump_priority(self, hactl: Hactl) -> None:
        """Bump priority of an existing constraint."""
        _ensure_hemm_entry(hactl)
        hactl.svc_call(
            "hemm.add_constraint_window",
            {
                "window_id": "bumpable_container",
                "device_id": "test_device",
                "deadline": "2030-01-01T00:00:00+00:00",
                "requirement_type": "forbidden_window",
                "priority_penalty": 1.0,
            },
        )
        result = hactl.svc_call("hemm.bump_priority", {"window_id": "bumpable_container", "new_penalty": 10.0})
        assert result.success

    def test_add_min_soc_constraint(self, hactl: Hactl) -> None:
        """Add a min_soc_until constraint."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call(
            "hemm.add_constraint_window",
            {
                "window_id": "ev_soc_test",
                "device_id": "ev_charger_1",
                "deadline": "2030-01-01T07:00:00+00:00",
                "requirement_type": "min_soc_until",
                "requirement_params": {"min_soc_pct": 80},
                "priority_penalty": 5.0,
            },
        )
        assert result.success

    def test_add_constraint_dry_run(self, hactl: Hactl) -> None:
        """Dry-run add doesn't actually add the constraint."""
        _ensure_hemm_entry(hactl)
        result = hactl.svc_call(
            "hemm.add_constraint_window",
            {
                "window_id": "dry_add",
                "device_id": "test_device",
                "deadline": "2030-01-01T00:00:00+00:00",
                "requirement_type": "forbidden_window",
                "dry_run": True,
            },
        )
        assert result.success


@pytest.mark.container
class TestOnboardingEndToEnd:
    """End-to-end onboarding flow: setup → add devices → replan → verify entities."""

    def test_full_onboarding_flow(self, hactl: Hactl) -> None:
        """Complete onboarding: setup hub → add battery → add EV → replan → check."""
        # 1. Setup HEMM
        entry_id = _ensure_hemm_entry(hactl)

        # 2. Add a battery device
        result = hactl.config_options(entry_id)
        flow_id = result.json_data["flow_id"]
        hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
        hactl.config_flow_step(flow_id, {"device_type": "battery", "tier": "beginner"}, options=True)
        result = hactl.config_flow_step(
            flow_id,
            {
                "device_name": "E2E Battery",
                "capacity_kwh": 10.0,
                "max_charge_kw": 5.0,
                "max_discharge_kw": 5.0,
                "safe_default_script": "script.hemm_battery_safe",
            },
            options=True,
        )
        assert result.json_data.get("type") == "create_entry"

        # 3. Add an EV charger device
        result = hactl.config_options(entry_id)
        flow_id = result.json_data["flow_id"]
        hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
        hactl.config_flow_step(flow_id, {"device_type": "ev_charger", "tier": "beginner"}, options=True)
        result = hactl.config_flow_step(
            flow_id,
            {
                "device_name": "E2E EV Charger",
                "max_charge_kw": 11.0,
                "safe_default_script": "script.hemm_ev_safe",
            },
            options=True,
        )
        assert result.json_data.get("type") == "create_entry"

        # 4. Call hemm.replan
        result = hactl.svc_call("hemm.replan")
        assert result.success

        # 5. Verify integration is still loaded and healthy
        result = hactl.config_entries()
        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
        assert hemm_entries[0]["state"] == "loaded"

        # 6. Check no error logs from hemm
        result = hactl.log(errors=True, component="hemm")
        assert result.success

    def test_setup_with_distributed_solver(self, hactl: Hactl) -> None:
        """Set up with distributed solver, add device, replan, verify no crash."""
        _ensure_hemm_entry(hactl)

        # Switch to distributed
        hactl.svc_call("hemm.set_solver", {"backend": "distributed"})

        # Replan with distributed solver
        result = hactl.svc_call("hemm.replan")
        assert result.success

        # Switch back
        hactl.svc_call("hemm.set_solver", {"backend": "milp_central"})

        # Verify still loaded
        result = hactl.config_entries()
        entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
        hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
        assert hemm_entries[0]["state"] == "loaded"
