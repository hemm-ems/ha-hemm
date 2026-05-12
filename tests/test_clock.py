"""Tests for the HA-side `HAClock` and Coordinator clock injection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hemm.coordinator import HemmCoordinator
from custom_components.hemm.time import HAClock


class TestHAClock:
    @pytest.mark.unit
    def test_now_is_tz_aware(self) -> None:
        c = HAClock()
        assert c.now().tzinfo is not None

    @pytest.mark.unit
    def test_now_follows_dt_util_patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point of HAClock: it tracks `dt_util.utcnow`.

        The Phase C time-warp harness will monkey-patch `dt_util.utcnow` to
        return virtual time; this test pins that contract.
        """
        instant = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        monkeypatch.setattr(
            "homeassistant.util.dt.utcnow",
            lambda: instant,
        )
        assert HAClock().now() == instant


class TestCoordinatorClockWiring:
    @pytest.mark.unit
    async def test_default_clock_is_haclock(self, hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
        coordinator = HemmCoordinator(hass, mock_config_entry)
        assert isinstance(coordinator.clock, HAClock)

    @pytest.mark.unit
    async def test_injected_clock_is_exposed_on_property(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """The clock passed at construction must be the one returned by
        `coordinator.clock` (which downstream code — services,
        constraint manager — reads from)."""

        class _StubClock:
            def __init__(self, t: datetime) -> None:
                self._t = t

            def now(self) -> datetime:
                return self._t

            def monotonic(self) -> float:
                return 0.0

        instant = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
        injected = _StubClock(instant)
        coordinator = HemmCoordinator(hass, mock_config_entry, clock=injected)
        assert coordinator.clock is injected
        assert coordinator.clock.now() == instant


class TestIntegrationClockReadsDtUtil:
    """End-to-end: a `dt_util` patch should change what HEMM sees as `now()`.

    This is the load-bearing invariant for the time-warp harness in Phase C.
    """

    @pytest.mark.unit
    async def test_dt_util_patch_changes_coordinator_clock(
        self,
        hass: HomeAssistant,
        init_integration: ConfigEntry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        coordinator: HemmCoordinator = hass.data["hemm"][init_integration.entry_id]
        virtual_now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
        monkeypatch.setattr("homeassistant.util.dt.utcnow", lambda: virtual_now)
        assert coordinator.clock.now() == virtual_now
