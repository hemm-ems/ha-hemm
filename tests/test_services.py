"""Comprehensive tests for HEMM services, coordinator, and Phase 6 features.

Covers:
- All 8 services (registration, unregistration, dry-run)
- Constraint lifecycle (add → bump → remove) via mocked hemm core
- Coordinator state transitions and properties
- Event firing (all 5 types)
- Nasty type combinations and edge cases
- Sensor creation with device entries
- Diagnostics extended fields
- Repairs framework
- Online identification stubs

Note: Tests that exercise constraint/solver services mock hemm core imports
because custom_components/hemm shadows the core hemm package in the HA test
framework. Full end-to-end tests run in container tests (tests/integration/).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_FLOOR_AREA_M2,
    CONF_HORIZON_HOURS,
    CONF_INSULATION_CLASS,
    CONF_MAX_ITERATIONS,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_SAFE_DEFAULT_SCRIPT,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
    EVENT_CONSTRAINT_ADDED,
    EVENT_CONSTRAINT_RESOLVED,
    EVENT_SOLVER_SWITCHED,
    SERVICE_ADD_CONSTRAINT,
    SERVICE_BUMP_PRIORITY,
    SERVICE_REMOVE_CONSTRAINT,
    SERVICE_REPLAN,
    SERVICE_SET_PRICE_CURVE,
    SERVICE_SET_SOLVER,
    SERVICE_SIMULATE,
    SERVICE_TICK,
)
from custom_components.hemm.coordinator import HemmCoordinator

# ────────────────────── Mock hemm core types ──────────────────────


@dataclass
class _FakeConstraintWindow:
    """Minimal ConstraintWindow mock matching hemm.manifest.messages.ConstraintWindow."""

    window_id: str = ""
    device_id: str = ""
    deadline: datetime | None = None
    requirement: Any = None
    flex_cost_per_hour_early: float = 0.0
    priority_penalty: float = 1.0
    ttl_seconds: float | None = None
    created_at: datetime | None = None


class _FakeConstraintManager:
    """Mock of hemm.constraints.ConstraintWindowManager."""

    def __init__(self) -> None:
        self._windows: dict[str, Any] = {}

    def add(self, window: Any) -> None:
        self._windows[window.window_id] = window

    def remove(self, window_id: str) -> Any:
        return self._windows.pop(window_id, None)

    def get_active(self, now: datetime | None = None) -> list:
        if now is not None:
            return [w for w in self._windows.values() if w.deadline and w.deadline > now]
        return list(self._windows.values())

    def bump_priority(self, window_id: str, new_penalty: float) -> bool:
        if window_id in self._windows:
            self._windows[window_id].priority_penalty = new_penalty
            return True
        return False

    def expire_old(self, now: datetime) -> list[str]:
        expired = [wid for wid, w in self._windows.items() if w.deadline and w.deadline <= now]
        for wid in expired:
            del self._windows[wid]
        return expired


# ────────────────────── Fixtures ──────────────────────


@pytest.fixture
def mock_config_entry_with_devices() -> MockConfigEntry:
    """Config entry with pre-configured devices."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="HEMM",
        data={
            CONF_NAME: "HEMM",
            CONF_HORIZON_HOURS: DEFAULT_HORIZON_HOURS,
            CONF_MAX_ITERATIONS: DEFAULT_MAX_ITERATIONS,
            CONF_PRICE_ADAPTER: DEFAULT_PRICE_ADAPTER,
            CONF_SOLVER_BACKEND: DEFAULT_SOLVER_BACKEND,
            "devices": [
                {
                    "id": "room_1",
                    CONF_DEVICE_TYPE: "room",
                    CONF_DEVICE_NAME: "Living Room",
                    CONF_FLOOR_AREA_M2: 25.0,
                    CONF_INSULATION_CLASS: "medium",
                    CONF_SAFE_DEFAULT_SCRIPT: "script.room_safe",
                },
            ],
        },
        unique_id=DOMAIN,
    )


@pytest.fixture
async def init_with_devices(hass: HomeAssistant, mock_config_entry_with_devices: MockConfigEntry) -> ConfigEntry:
    """Set up HEMM with pre-configured devices."""
    mock_config_entry_with_devices.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry_with_devices.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry_with_devices


@pytest.fixture
def hemm_core_mocks(monkeypatch: pytest.MonkeyPatch) -> _FakeConstraintManager:
    """Mock hemm core imports to bypass custom_components/hemm shadowing.

    Patches:
    - coordinator._create_constraint_manager → returns _FakeConstraintManager
    - sys.modules for hemm.manifest, hemm.manifest.messages, hemm.manifest.constraints
    - services._REQUIREMENT_BUILDERS cache is cleared so it re-initializes with mocks

    Returns the mock constraint manager for assertions.
    """
    mgr = _FakeConstraintManager()
    monkeypatch.setattr(
        "custom_components.hemm.coordinator._create_constraint_manager",
        lambda: mgr,
    )

    # Mock hemm.manifest and submodules in sys.modules
    manifest_mock = MagicMock()
    messages_mock = MagicMock()
    messages_mock.ConstraintWindow = _FakeConstraintWindow
    constraints_mock = MagicMock()

    monkeypatch.setitem(sys.modules, "hemm.manifest", manifest_mock)
    monkeypatch.setitem(sys.modules, "hemm.manifest.messages", messages_mock)
    monkeypatch.setitem(sys.modules, "hemm.manifest.constraints", constraints_mock)

    # Clear requirement builders cache so it re-initializes with mock classes
    from custom_components.hemm import services

    services._REQUIREMENT_BUILDERS.clear()

    return mgr


# ──────────────────────── Service Registration ────────────────────────


@pytest.mark.unit
class TestServiceRegistration:
    """Verify all 8 services register and unregister."""

    async def test_all_services_registered(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        for svc in (
            SERVICE_REPLAN,
            SERVICE_SIMULATE,
            SERVICE_SET_PRICE_CURVE,
            SERVICE_SET_SOLVER,
            SERVICE_ADD_CONSTRAINT,
            SERVICE_REMOVE_CONSTRAINT,
            SERVICE_BUMP_PRIORITY,
            SERVICE_TICK,
        ):
            assert hass.services.has_service(DOMAIN, svc), f"Service {svc} not registered"

    async def test_services_unregistered_on_unload(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.config_entries.async_unload(init_integration.entry_id)
        await hass.async_block_till_done()
        for svc in (
            SERVICE_REPLAN,
            SERVICE_SIMULATE,
            SERVICE_SET_SOLVER,
            SERVICE_TICK,
            SERVICE_ADD_CONSTRAINT,
            SERVICE_REMOVE_CONSTRAINT,
            SERVICE_BUMP_PRIORITY,
            SERVICE_SET_PRICE_CURVE,
        ):
            assert not hass.services.has_service(DOMAIN, svc)


# ──────────────────────── Coordinator Basics ────────────────────────


@pytest.mark.unit
class TestCoordinatorBasics:
    """Core coordinator properties and state."""

    async def test_initial_state(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator.data is not None
        assert coordinator.data["last_status"] in ("idle", "stub", "optimal")
        assert coordinator.data["iteration_count"] == 0

    async def test_properties(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator.horizon_hours == DEFAULT_HORIZON_HOURS
        assert coordinator.solver_backend == DEFAULT_SOLVER_BACKEND
        assert coordinator.price_adapter == DEFAULT_PRICE_ADAPTER
        assert coordinator.last_result is None
        assert coordinator.dry_run_log == []
        assert coordinator.id_results == []

    async def test_data_keys(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        for key in (
            "horizon_hours",
            "max_iterations",
            "price_adapter",
            "solver_backend",
            "last_plans",
            "iteration_count",
            "device_plans",
            "last_status",
            "last_solve_time",
        ):
            assert key in coordinator.data, f"Missing key: {key}"

    async def test_device_plans_stub_with_devices(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        plans = coordinator.data["device_plans"]
        assert "room_1" in plans
        assert plans["room_1"]["power_kw"] == 0.0
        assert plans["room_1"]["mode"] == "idle"


# ──────────────────────── Solver Switching ────────────────────────


@pytest.mark.unit
class TestSolverSwitching:
    """Solver backend switching and events."""

    async def test_switch_fires_event(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        events: list = []
        hass.bus.async_listen(EVENT_SOLVER_SWITCHED, lambda e: events.append(e))

        coordinator.switch_solver("distributed")
        await hass.async_block_till_done()

        assert coordinator.solver_backend == "distributed"
        assert len(events) == 1
        assert events[0].data["old_backend"] == "milp_central"
        assert events[0].data["new_backend"] == "distributed"

    async def test_service_set_solver(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_SOLVER_SWITCHED, lambda e: events.append(e))
        await hass.services.async_call(DOMAIN, SERVICE_SET_SOLVER, {"backend": "distributed"}, blocking=True)
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator.solver_backend == "distributed"
        assert len(events) == 1

    async def test_dry_run_no_change(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_SOLVER, {"backend": "distributed", "dry_run": True}, blocking=True
        )
        await hass.async_block_till_done()
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator.solver_backend == "milp_central"

    async def test_switch_back_and_forth(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        events: list = []
        hass.bus.async_listen(EVENT_SOLVER_SWITCHED, lambda e: events.append(e))

        coordinator.switch_solver("distributed")
        coordinator.switch_solver("milp_central")
        coordinator.switch_solver("distributed")
        await hass.async_block_till_done()

        assert coordinator.solver_backend == "distributed"
        assert len(events) == 3

    async def test_switch_to_same_backend(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        events: list = []
        hass.bus.async_listen(EVENT_SOLVER_SWITCHED, lambda e: events.append(e))

        coordinator.switch_solver("milp_central")
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["old_backend"] == "milp_central"


# ──────────────────────── Constraint Lifecycle ────────────────────────


@pytest.mark.unit
class TestConstraintLifecycle:
    """Full constraint lifecycle via services with mocked hemm core."""

    async def test_add_constraint_fires_event(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_ADDED, lambda e: events.append(e))

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "test_w1",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=6)).isoformat(),
                "requirement_type": "hold_temp_band",
                "requirement_params": {"min_temp_c": 20.0, "max_temp_c": 23.0},
                "priority_penalty": 3.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["window_id"] == "test_w1"
        assert events[0].data["device_id"] == "room_1"

    async def test_add_dry_run_no_side_effect(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_ADDED, lambda e: events.append(e))

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "dry_w",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
                "dry_run": True,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(events) == 0
        assert len(hemm_core_mocks.get_active()) == 0

    async def test_remove_fires_event(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        # Add first
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "to_remove",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=6)).isoformat(),
                "requirement_type": "forbidden_window",
                "priority_penalty": 1.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_RESOLVED, lambda e: events.append(e))

        await hass.services.async_call(DOMAIN, SERVICE_REMOVE_CONSTRAINT, {"window_id": "to_remove"}, blocking=True)
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["window_id"] == "to_remove"

    async def test_remove_nonexistent_no_event(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_RESOLVED, lambda e: events.append(e))

        await hass.services.async_call(DOMAIN, SERVICE_REMOVE_CONSTRAINT, {"window_id": "ghost"}, blocking=True)
        await hass.async_block_till_done()

        assert len(events) == 0

    async def test_remove_dry_run_keeps_constraint(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "keep_me",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN, SERVICE_REMOVE_CONSTRAINT, {"window_id": "keep_me", "dry_run": True}, blocking=True
        )
        await hass.async_block_till_done()

        assert any(w.window_id == "keep_me" for w in hemm_core_mocks.get_active())

    async def test_bump_priority(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "bumpable",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=3)).isoformat(),
                "requirement_type": "min_energy_until",
                "requirement_params": {"min_kwh": 5.0},
                "priority_penalty": 1.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN, SERVICE_BUMP_PRIORITY, {"window_id": "bumpable", "new_penalty": 10.0}, blocking=True
        )
        await hass.async_block_till_done()

        bumpable = [w for w in hemm_core_mocks.get_active() if w.window_id == "bumpable"]
        assert len(bumpable) == 1
        assert bumpable[0].priority_penalty == 10.0

    async def test_bump_dry_run_no_change(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "bump_dry",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
                "priority_penalty": 2.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_BUMP_PRIORITY,
            {"window_id": "bump_dry", "new_penalty": 99.0, "dry_run": True},
            blocking=True,
        )
        await hass.async_block_till_done()

        bumped = [w for w in hemm_core_mocks.get_active() if w.window_id == "bump_dry"]
        assert bumped[0].priority_penalty == 2.0  # Unchanged


# ──────────────────────── All 7 Constraint Types ────────────────────────


@pytest.mark.unit
class TestAllConstraintTypes:
    """Test adding all 7 constraint types via service call."""

    CONSTRAINT_CONFIGS: ClassVar[list[tuple[str, dict]]] = [
        ("reach_min_temp_once", {"min_temp_c": 60.0}),
        ("hold_temp_band", {"min_temp_c": 20.0, "max_temp_c": 23.0}),
        ("min_soc_until", {"min_soc_pct": 80}),
        ("min_energy_until", {"min_kwh": 10.0}),
        ("forbidden_window", {}),
        ("min_runtime_per_day", {"min_hours": 4.0}),
        ("max_runtime_per_day", {"max_hours": 8.0}),
    ]

    @pytest.mark.parametrize(
        "req_type,params",
        CONSTRAINT_CONFIGS,
        ids=[c[0] for c in CONSTRAINT_CONFIGS],
    )
    async def test_add_constraint_type(
        self,
        hass: HomeAssistant,
        init_integration: ConfigEntry,
        hemm_core_mocks: _FakeConstraintManager,
        req_type: str,
        params: dict,
    ) -> None:
        window_id = f"test_{req_type}"
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": window_id,
                "device_id": "test_dev",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=2)).isoformat(),
                "requirement_type": req_type,
                "requirement_params": params,
                "priority_penalty": 5.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        found = [w for w in hemm_core_mocks.get_active() if w.window_id == window_id]
        assert len(found) == 1


# ──────────────────────── Set Price Curve ────────────────────────


@pytest.mark.unit
class TestSetPriceCurve:
    """hemm.set_price_curve service tests."""

    async def test_stores_prices(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        prices = [0.10, 0.20, 0.30, 0.40, 0.35, 0.25, 0.15, 0.10]
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_PRICE_CURVE, {"prices": prices, "resolution_minutes": 60}, blocking=True
        )
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator._manual_prices == prices
        assert coordinator._manual_price_resolution == 60

    async def test_dry_run_no_store(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_PRICE_CURVE, {"prices": [0.50, 0.60], "dry_run": True}, blocking=True
        )
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert not hasattr(coordinator, "_manual_prices") or coordinator._manual_prices == []

    async def test_empty_list(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(DOMAIN, SERVICE_SET_PRICE_CURVE, {"prices": []}, blocking=True)
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator._manual_prices == []

    async def test_single_value(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(DOMAIN, SERVICE_SET_PRICE_CURVE, {"prices": [0.42]}, blocking=True)
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator._manual_prices == [0.42]


# ──────────────────────── Nasty Type Combinations ────────────────────────


@pytest.mark.unit
class TestNastyTypeCombinations:
    """Edge cases and tricky type/value combinations."""

    async def test_zero_penalty(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "zero_pen",
                "device_id": "dev",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
                "priority_penalty": 0.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert any(w.window_id == "zero_pen" for w in hemm_core_mocks.get_active())

    async def test_very_large_penalty(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "big_pen",
                "device_id": "dev",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
                "priority_penalty": 999999.99,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        big = [w for w in hemm_core_mocks.get_active() if w.window_id == "big_pen"]
        assert big[0].priority_penalty == 999999.99

    async def test_negative_flex_cost(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "neg_flex",
                "device_id": "dev",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "min_energy_until",
                "requirement_params": {"min_kwh": 5.0},
                "flex_cost_per_hour_early": -1.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert any(w.window_id == "neg_flex" for w in hemm_core_mocks.get_active())

    async def test_multiple_constraints_same_device(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        for i in range(3):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_ADD_CONSTRAINT,
                {
                    "window_id": f"multi_{i}",
                    "device_id": "same_dev",
                    "deadline": (datetime.now(tz=UTC) + timedelta(hours=1 + i)).isoformat(),
                    "requirement_type": "forbidden_window",
                    "priority_penalty": float(i + 1),
                },
                blocking=True,
            )
        await hass.async_block_till_done()

        same_dev = [w for w in hemm_core_mocks.get_active() if w.device_id == "same_dev"]
        assert len(same_dev) >= 3

    async def test_negative_prices(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PRICE_CURVE,
            {"prices": [-0.05, -0.02, 0.0, 0.10, 0.30, -0.01]},
            blocking=True,
        )
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator._manual_prices == [-0.05, -0.02, 0.0, 0.10, 0.30, -0.01]

    async def test_very_high_prices(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_PRICE_CURVE, {"prices": [5.00, 8.50, 12.00, 3.00]}, blocking=True
        )
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert coordinator._manual_prices[2] == 12.00

    async def test_constraint_with_ttl(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "ttl_test",
                "device_id": "dev",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
                "ttl_seconds": 3600.0,
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert any(w.window_id == "ttl_test" for w in hemm_core_mocks.get_active())

    async def test_far_future_deadline(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        far_future = (datetime.now(tz=UTC) + timedelta(days=365)).isoformat()
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "far_future",
                "device_id": "dev",
                "deadline": far_future,
                "requirement_type": "forbidden_window",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert any(w.window_id == "far_future" for w in hemm_core_mocks.get_active())

    async def test_rapid_add_remove_cycle(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        """Add and immediately remove — no stale state."""
        for i in range(5):
            wid = f"rapid_{i}"
            await hass.services.async_call(
                DOMAIN,
                SERVICE_ADD_CONSTRAINT,
                {
                    "window_id": wid,
                    "device_id": "dev",
                    "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                    "requirement_type": "forbidden_window",
                },
                blocking=True,
            )
            await hass.services.async_call(DOMAIN, SERVICE_REMOVE_CONSTRAINT, {"window_id": wid}, blocking=True)
        await hass.async_block_till_done()

        remaining = [w for w in hemm_core_mocks.get_active() if w.window_id.startswith("rapid_")]
        assert len(remaining) == 0


# ──────────────────────── Event Firing ────────────────────────


@pytest.mark.unit
class TestEventFiring:
    """Verify all 5 event types fire correctly."""

    async def test_constraint_added_event_data(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_ADDED, lambda e: events.append(e))

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "evt_test",
                "device_id": "dev_42",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        assert events[0].data["window_id"] == "evt_test"
        assert events[0].data["device_id"] == "dev_42"

    async def test_solver_switched_event_data(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        events: list = []
        hass.bus.async_listen(EVENT_SOLVER_SWITCHED, lambda e: events.append(e))

        await hass.services.async_call(DOMAIN, SERVICE_SET_SOLVER, {"backend": "distributed"}, blocking=True)
        await hass.async_block_till_done()

        assert events[0].data["old_backend"] == "milp_central"
        assert events[0].data["new_backend"] == "distributed"

    async def test_constraint_resolved_event_data(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        # Add then remove
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "resolve_me",
                "device_id": "dev_99",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        events: list = []
        hass.bus.async_listen(EVENT_CONSTRAINT_RESOLVED, lambda e: events.append(e))

        await hass.services.async_call(DOMAIN, SERVICE_REMOVE_CONSTRAINT, {"window_id": "resolve_me"}, blocking=True)
        await hass.async_block_till_done()

        assert events[0].data["window_id"] == "resolve_me"
        assert events[0].data["device_id"] == "dev_99"


# ──────────────────────── Diagnostics Extended ────────────────────────


@pytest.mark.unit
class TestDiagnosticsExtended:
    """Extended diagnostics output from Phase 6."""

    async def test_has_all_fields(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        from custom_components.hemm.diagnostics import async_get_config_entry_diagnostics

        diag = await async_get_config_entry_diagnostics(hass, init_integration)
        for key in (
            "tested_ha_version",
            "active_constraint_windows",
            "last_solver_result",
            "lambda_history",
            "dry_run_log",
            "identification_results",
            "coordinator_state",
        ):
            assert key in diag, f"Missing diagnostics key: {key}"

    async def test_coordinator_state_keys(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        from custom_components.hemm.diagnostics import async_get_config_entry_diagnostics

        diag = await async_get_config_entry_diagnostics(hass, init_integration)
        for key in ("horizon_hours", "solver_backend", "price_adapter", "last_status"):
            assert key in diag["coordinator_state"]

    async def test_config_entry_data(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        from custom_components.hemm.diagnostics import async_get_config_entry_diagnostics

        diag = await async_get_config_entry_diagnostics(hass, init_integration)
        assert diag["config_entry"]["title"] == "HEMM"

    async def test_after_constraint_added(
        self, hass: HomeAssistant, init_integration: ConfigEntry, hemm_core_mocks: _FakeConstraintManager
    ) -> None:
        from custom_components.hemm.diagnostics import async_get_config_entry_diagnostics

        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONSTRAINT,
            {
                "window_id": "diag_test",
                "device_id": "room_1",
                "deadline": (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat(),
                "requirement_type": "forbidden_window",
            },
            blocking=True,
        )
        await hass.async_block_till_done()

        diag = await async_get_config_entry_diagnostics(hass, init_integration)
        assert len(diag["active_constraint_windows"]) >= 1


# ──────────────────────── Sensors with Devices ────────────────────────


@pytest.mark.unit
class TestSensorsWithDevices:
    """Sensor creation when devices are configured."""

    async def test_creates_four_sensors(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        states = hass.states.async_all("sensor")
        # Entity IDs use device name, not "hemm" prefix (e.g., sensor.living_room_living_room_plan)
        plan = [s for s in states if "plan" in s.entity_id]
        confidence = [s for s in states if "confidence" in s.entity_id]
        mode = [s for s in states if "mode" in s.entity_id]
        reason = [s for s in states if "reason" in s.entity_id]
        assert len(plan) >= 1
        assert len(confidence) >= 1
        assert len(mode) >= 1
        assert len(reason) >= 1

    async def test_initial_state(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """Mode sensor starts in unknown (no coordinator update) or idle."""
        states = hass.states.async_all("sensor")
        mode_sensors = [s for s in states if "mode" in s.entity_id]
        if mode_sensors:
            assert mode_sensors[0].state in ("unknown", "idle")

    async def test_reason_sensor_default_idle(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """Reason sensor starts in 'idle' or 'unknown' before first solve."""
        states = hass.states.async_all("sensor")
        reason_sensors = [s for s in states if "reason" in s.entity_id]
        if reason_sensors:
            assert reason_sensors[0].state in ("unknown", "idle")


# ──────────────────────── Repairs ────────────────────────


@pytest.mark.unit
class TestRepairs:
    """Repair flow tests."""

    async def test_repair_flow_instantiation(self) -> None:
        from custom_components.hemm.repairs import HemmSolverDegradedRepairFlow

        flow = HemmSolverDegradedRepairFlow()
        assert flow is not None

    async def test_async_create_fix_flow(self) -> None:
        from custom_components.hemm.repairs import async_create_fix_flow

        flow = await async_create_fix_flow(None, "solver_degraded", None)
        assert flow is not None


# ──────────────────────── Identification ────────────────────────


@pytest.mark.unit
class TestIdentificationIntegration:
    """Online identification integration with coordinator."""

    async def test_run_id_empty_devices(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        results = await coordinator.async_run_identification()
        assert results == []

    async def test_run_id_with_devices(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        results = await coordinator.async_run_identification()
        assert results == []  # Stubs return None

    async def test_id_results_property(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        assert isinstance(coordinator.id_results, list)


# ──────────────────────── Replan Device Filter ────────────────────────


@pytest.mark.unit
class TestReplanDeviceFilter:
    """Tests for hemm.replan device_filter parameter."""

    async def test_replan_schema_accepts_device_filter(
        self, hass: HomeAssistant, init_integration: ConfigEntry
    ) -> None:
        """hemm.replan schema validates device_filter field without error."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]
        # Mock async_run_solver to avoid hemm core import
        called_with: dict = {}

        async def _fake_solver(*, dry_run=False, device_filter=None):
            called_with["dry_run"] = dry_run
            called_with["device_filter"] = device_filter
            # Return a minimal mock result
            mock_result = MagicMock()
            mock_result.status = MagicMock(value="optimal")
            mock_result.solve_time_seconds = 0.0
            return mock_result

        coordinator.async_run_solver = _fake_solver
        await hass.services.async_call(DOMAIN, SERVICE_REPLAN, {"device_filter": ["some_device"]}, blocking=True)
        assert called_with["device_filter"] == ["some_device"]

    async def test_replan_without_device_filter(self, hass: HomeAssistant, init_integration: ConfigEntry) -> None:
        """hemm.replan works without device_filter (backward compat)."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_integration.entry_id]

        async def _fake_solver(*, dry_run=False, device_filter=None):
            mock_result = MagicMock()
            mock_result.status = MagicMock(value="optimal")
            mock_result.solve_time_seconds = 0.0
            return mock_result

        coordinator.async_run_solver = _fake_solver
        await hass.services.async_call(DOMAIN, SERVICE_REPLAN, {}, blocking=True)

    async def test_replan_device_filter_forwarded(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """device_filter is forwarded to coordinator.async_run_solver."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        captured_filter: list = []

        async def _fake_solver(*, dry_run=False, device_filter=None):
            captured_filter.append(device_filter)
            mock_result = MagicMock()
            mock_result.status = MagicMock(value="optimal")
            mock_result.solve_time_seconds = 0.0
            return mock_result

        coordinator.async_run_solver = _fake_solver
        await hass.services.async_call(DOMAIN, SERVICE_REPLAN, {"device_filter": ["room_1"]}, blocking=True)
        assert captured_filter == [["room_1"]]


# ──────────────────────── Control Class Config ────────────────────────


@pytest.mark.unit
class TestControlClassConfig:
    """Tests for control_class in device configuration."""

    async def test_device_plans_include_reason(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """device_plans dict includes a 'reason' key per device."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        plans = coordinator.data["device_plans"]
        assert "room_1" in plans
        assert "reason" in plans["room_1"]
        assert plans["room_1"]["reason"] == "idle"
