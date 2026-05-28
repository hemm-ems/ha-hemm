"""Contract tests: HA constants stay in sync with hemm_core enums.

These tests catch vocabulary drift between the HA integration and the
core library, so that adding/renaming an enum member in hemm_core will
immediately surface here.
"""

from __future__ import annotations

import hemm_core.manifest.messages as _msgs
import hemm_core.manifest.types as _types

from custom_components.hemm.const import (
    PLAN_REASONS,
    DeviceType,
)


def test_device_type_values_match_manifest_type() -> None:
    """DeviceType must mirror ManifestType values exactly."""
    ha_values = {m.value for m in DeviceType}
    core_values = {m.value for m in _types.ManifestType}
    assert ha_values == core_values, (
        f"DeviceType drift: HA-only={ha_values - core_values}, core-only={core_values - ha_values}"
    )


def test_plan_reasons_match_plan_reason_enum() -> None:
    """PLAN_REASONS list must contain all PlanReason values."""
    core_values = [r.value for r in _msgs.PlanReason]
    assert core_values == PLAN_REASONS


def test_control_class_imported_from_core() -> None:
    """ControlClass must be the real core enum, not a local mirror."""
    from custom_components.hemm.const import ControlClass

    assert ControlClass is _types.ControlClass


def test_core_occupants_package_importable() -> None:
    """The integration test environment installs a core build with sim occupants."""
    import importlib

    occupants = importlib.import_module("hemm_core.sim.occupants")

    assert occupants.HouseholdProfile is not None
