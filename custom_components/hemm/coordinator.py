"""DataUpdateCoordinator for HEMM."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=15)


class HemmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """HEMM DataUpdateCoordinator — runs optimization on schedule."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self._horizon_hours: int = entry.options.get(
            CONF_HORIZON_HOURS,
            entry.data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
        )
        self._max_iterations: int = entry.options.get(
            CONF_MAX_ITERATIONS,
            entry.data.get(CONF_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS),
        )
        self._price_adapter: str = entry.options.get(
            CONF_PRICE_ADAPTER,
            entry.data.get(CONF_PRICE_ADAPTER, DEFAULT_PRICE_ADAPTER),
        )
        self._solver_backend: str = entry.options.get(
            CONF_SOLVER_BACKEND,
            entry.data.get(CONF_SOLVER_BACKEND, DEFAULT_SOLVER_BACKEND),
        )

    @property
    def horizon_hours(self) -> int:
        """Return optimization horizon in hours."""
        return self._horizon_hours

    @property
    def solver_backend(self) -> str:
        """Return active solver backend name."""
        return self._solver_backend

    @property
    def price_adapter(self) -> str:
        """Return active price adapter name."""
        return self._price_adapter

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data — stub: returns current config as state."""
        return {
            "horizon_hours": self._horizon_hours,
            "max_iterations": self._max_iterations,
            "price_adapter": self._price_adapter,
            "solver_backend": self._solver_backend,
            "last_plans": [],
            "iteration_count": 0,
        }
