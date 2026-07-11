"""Online Identification framework for HEMM.

Each device type has an Identifier that can refine model parameters
based on observed data. When parameters change significantly, a repair
issue is raised for user confirmation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class IdentificationResult:
    """Result of an online identification run."""

    device_id: str
    parameter_updates: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    message: str = ""


class DeviceIdentifier(ABC):
    """Abstract base class for online device identification."""

    @abstractmethod
    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Run identification on observed data.

        Returns an IdentificationResult if parameters should be updated,
        or None if current parameters are still valid.
        """

    @property
    @abstractmethod
    def device_type(self) -> str:
        """Return the device type this identifier handles."""


class RoomIdentifier(DeviceIdentifier):
    """Online ID for Room — refines thermal parameters from temperature data."""

    @property
    def device_type(self) -> str:
        return "room"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine U-value and thermal mass from temperature curves."""
        return None


class HeatPumpIdentifier(DeviceIdentifier):
    """Online ID for HeatPump — refines COP map from measured data."""

    @property
    def device_type(self) -> str:
        return "heat_pump"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine COP curve from power/heat measurements."""
        return None


class WaterHeaterIdentifier(DeviceIdentifier):
    """Online ID for WaterHeater — refines loss coefficient from standby data."""

    @property
    def device_type(self) -> str:
        return "water_heater"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine loss parameters from standby temperature decay."""
        return None


class BatteryIdentifier(DeviceIdentifier):
    """Online ID for Battery — refines efficiency from charge/discharge cycles."""

    @property
    def device_type(self) -> str:
        return "battery"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine efficiency from measured round-trip energy."""
        return None


class PVForecastIdentifier(DeviceIdentifier):
    """Online ID for PVForecast — calibrates forecast bias."""

    @property
    def device_type(self) -> str:
        return "pv_forecast"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would calibrate forecast adapter using actual production data."""
        return None


class EVChargerIdentifier(DeviceIdentifier):
    """Online ID for EVCharger — refines charging curve model."""

    @property
    def device_type(self) -> str:
        return "ev_charger"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine charging curve from observed SoC progression."""
        return None


class ThermostatLoadIdentifier(DeviceIdentifier):
    """Online ID for ThermostatLoad — refines duty cycle model."""

    @property
    def device_type(self) -> str:
        return "thermostat_load"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine on/off duty cycle from power measurements."""
        return None


class PassiveLoadIdentifier(DeviceIdentifier):
    """Online ID for PassiveLoad — refines daily consumption estimate."""

    @property
    def device_type(self) -> str:
        return "passive_load"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine typical_daily_kwh from observed consumption patterns."""
        return None


class PoolPumpIdentifier(DeviceIdentifier):
    """Online ID for PoolPump — refines power draw/runtime model."""

    @property
    def device_type(self) -> str:
        return "pool_pump"

    def identify(self, observations: list[dict[str, Any]]) -> IdentificationResult | None:
        """Stub: would refine max_power_kw from observed runtime/power draw."""
        return None


# Registry of identifiers per device type
IDENTIFIER_REGISTRY: dict[str, type[DeviceIdentifier]] = {
    "room": RoomIdentifier,
    "thermostat_load": ThermostatLoadIdentifier,
    "heat_pump": HeatPumpIdentifier,
    "water_heater": WaterHeaterIdentifier,
    "battery": BatteryIdentifier,
    "pv_forecast": PVForecastIdentifier,
    "ev_charger": EVChargerIdentifier,
    "passive_load": PassiveLoadIdentifier,
    "pool_pump": PoolPumpIdentifier,
}


def get_identifier(device_type: str) -> DeviceIdentifier | None:
    """Get an identifier instance for the given device type."""
    cls = IDENTIFIER_REGISTRY.get(device_type)
    if cls is None:
        _LOGGER.warning("No identifier registered for device type: %s", device_type)
        return None
    return cls()
