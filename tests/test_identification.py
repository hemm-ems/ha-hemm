"""Tests for the HEMM online identification framework."""

from __future__ import annotations

import pytest

from custom_components.hemm.identification import (
    IDENTIFIER_REGISTRY,
    BatteryIdentifier,
    EVChargerIdentifier,
    HeatPumpIdentifier,
    PVForecastIdentifier,
    RoomIdentifier,
    ThermostatLoadIdentifier,
    WaterHeaterIdentifier,
    get_identifier,
)


@pytest.mark.unit
@pytest.mark.req("006:FR-002")
def test_all_device_types_have_identifier() -> None:
    """Test that all 7 device types have an identifier registered."""
    expected_types = {
        "room",
        "thermostat_load",
        "heat_pump",
        "water_heater",
        "battery",
        "pv_forecast",
        "ev_charger",
        "passive_load",
    }
    assert set(IDENTIFIER_REGISTRY.keys()) == expected_types


@pytest.mark.unit
def test_get_identifier_returns_correct_type() -> None:
    """Test that get_identifier returns the correct identifier class."""
    assert isinstance(get_identifier("room"), RoomIdentifier)
    assert isinstance(get_identifier("heat_pump"), HeatPumpIdentifier)
    assert isinstance(get_identifier("water_heater"), WaterHeaterIdentifier)
    assert isinstance(get_identifier("battery"), BatteryIdentifier)
    assert isinstance(get_identifier("pv_forecast"), PVForecastIdentifier)
    assert isinstance(get_identifier("ev_charger"), EVChargerIdentifier)
    assert isinstance(get_identifier("thermostat_load"), ThermostatLoadIdentifier)


@pytest.mark.unit
def test_get_identifier_unknown_type() -> None:
    """Test that get_identifier returns None for unknown types."""
    assert get_identifier("unknown_device") is None


@pytest.mark.unit
def test_identifier_stub_returns_none() -> None:
    """Test that stub identifiers return None (no update needed)."""
    for device_type in IDENTIFIER_REGISTRY:
        identifier = get_identifier(device_type)
        assert identifier is not None
        result = identifier.identify([])
        assert result is None


@pytest.mark.unit
@pytest.mark.req("006:FR-001")
def test_identifier_device_type_property() -> None:
    """Test that each identifier reports its device_type correctly."""
    for device_type, cls in IDENTIFIER_REGISTRY.items():
        instance = cls()
        assert instance.device_type == device_type
