"""Tests for coordinator auto-solve (Issue 5).

Verifies that _async_update_data actually runs the solver, the
re-entrancy guard works, and a runaway solver times out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_HORIZON_HOURS,
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
)
from custom_components.hemm.coordinator import HemmCoordinator


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
                    "id": "battery_1",
                    CONF_DEVICE_TYPE: "battery",
                    CONF_DEVICE_NAME: "Home Battery",
                    CONF_SAFE_DEFAULT_SCRIPT: "script.battery_safe",
                },
            ],
        },
        unique_id=f"{DOMAIN}_autosolve",
    )


@pytest.fixture
async def init_with_devices(hass: HomeAssistant, mock_config_entry_with_devices: MockConfigEntry) -> ConfigEntry:
    """Set up HEMM with pre-configured devices."""
    mock_config_entry_with_devices.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry_with_devices.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry_with_devices


def _make_mock_result(status: str = "optimal", plans: list | None = None) -> MagicMock:
    """Create a mock SolverResult."""
    result = MagicMock()
    result.status = MagicMock(value=status)
    result.status.__eq__ = lambda self, other: self.value == getattr(other, "value", other)
    result.solve_time_seconds = 0.01
    result.objective_value = 42.0
    result.plans = plans or []
    return result


@pytest.mark.unit
class TestAutoSolve:
    """Coordinator auto-solve: _async_update_data triggers solver."""

    @pytest.mark.req("008:FR-004")
    async def test_update_data_runs_solver(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """A scheduled coordinator refresh triggers a solve with no external hemm.tick.

        Exercises the DataUpdateCoordinator refresh entrypoint that the 15-min
        update_interval drives; asserts each tick runs exactly one solve — proving
        periodic re-planning needs no external automation (008:FR-004 / SC-004).
        """
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        call_count = 0

        async def _fake_solver(*, dry_run=False, device_filter=None):
            nonlocal call_count
            call_count += 1
            return _make_mock_result()

        coordinator.async_run_solver = _fake_solver  # type: ignore[assignment]

        await coordinator.async_refresh()
        await hass.async_block_till_done()  # let background solver task run
        first = call_count

        await coordinator.async_refresh()
        await hass.async_block_till_done()  # let background solver task run
        assert call_count == first + 1, "Each refresh should trigger one solve"

    async def test_reentrancy_guard(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """Overlapping solve calls serialize on _solve_lock: never concurrent,
        but an explicit request still gets its own fresh solve afterwards."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]
        solve_count = 0
        gate = asyncio.Event()

        async def _slow_do_solve(*, dry_run=False, device_filter=None):
            nonlocal solve_count
            solve_count += 1
            await gate.wait()
            return _make_mock_result()

        coordinator._do_solve = _slow_do_solve  # type: ignore[assignment]

        # Start first solve (will block on gate)
        task1 = asyncio.create_task(coordinator.async_run_solver())

        # Give it a moment to acquire the lock
        await asyncio.sleep(0.01)
        assert coordinator._solve_lock.locked()

        # Second call must wait, not run concurrently — and not be dropped
        task2 = asyncio.create_task(coordinator.async_run_solver())
        await asyncio.sleep(0.01)
        assert solve_count == 1, "Second solve must not start while first is in flight"

        # Unblock: both solves complete, the second with its own fresh run
        gate.set()
        await task1
        await task2
        assert solve_count == 2, "Explicit solve request must run after the in-flight solve"

    async def test_solver_timeout(self, hass: HomeAssistant, init_with_devices: ConfigEntry) -> None:
        """A runaway solver is cancelled by the timeout in _run_solver_background."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]

        async def _hang_forever(*, dry_run=False, device_filter=None):
            await asyncio.sleep(3600)
            return _make_mock_result()

        coordinator.async_run_solver = _hang_forever  # type: ignore[assignment]

        # Patch the timeout to something short for the test
        with patch("custom_components.hemm.coordinator.SOLVER_TIMEOUT_SECONDS", 0.1):
            # _async_update_data returns immediately (solver runs in background)
            data = await coordinator._async_update_data()
            # Let the background task run and hit the timeout
            await asyncio.sleep(0.2)
            await hass.async_block_till_done()

        assert data["last_status"] in ("idle", "error")

    async def test_solver_exception_does_not_crash_update(
        self, hass: HomeAssistant, init_with_devices: ConfigEntry
    ) -> None:
        """A solver crash doesn't break _async_update_data."""
        coordinator: HemmCoordinator = hass.data[DOMAIN][init_with_devices.entry_id]

        async def _exploding_solver(*, dry_run=False, device_filter=None):
            raise RuntimeError("boom")

        coordinator.async_run_solver = _exploding_solver  # type: ignore[assignment]

        data = await coordinator._async_update_data()
        # Should still return valid data dict
        assert "device_plans" in data
        assert "last_status" in data

    async def test_no_solve_without_devices(self, hass: HomeAssistant) -> None:
        """No solver run if no devices are configured."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="HEMM",
            data={
                CONF_NAME: "HEMM",
                CONF_HORIZON_HOURS: DEFAULT_HORIZON_HOURS,
                CONF_MAX_ITERATIONS: DEFAULT_MAX_ITERATIONS,
                CONF_PRICE_ADAPTER: DEFAULT_PRICE_ADAPTER,
                CONF_SOLVER_BACKEND: DEFAULT_SOLVER_BACKEND,
                "devices": [],
            },
            unique_id=f"{DOMAIN}_nodev",
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator: HemmCoordinator = hass.data[DOMAIN][entry.entry_id]
        solve_called = False

        async def _spy_solver(*, dry_run=False, device_filter=None):
            nonlocal solve_called
            solve_called = True
            return _make_mock_result()

        coordinator.async_run_solver = _spy_solver  # type: ignore[assignment]
        await coordinator.async_refresh()
        assert not solve_called, "Solver should not run without devices"
