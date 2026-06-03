"""Integration guard for exported manifest primitive metadata."""

from __future__ import annotations

import pytest
from hemm_core.manifest.schema_export import get_all_schemas, get_manifest_schema
from hemm_core.manifest.validator import validate_manifest

from custom_components.hemm.const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_MAX_POWER_KW,
    CONF_SAFE_DEFAULT_SCRIPT,
    DeviceType,
)
from custom_components.hemm.manifest_builder import build_manifest

EXPECTED_PRIMITIVES = {
    "battery": {"storage"},
    "heat_pump": {"converter"},
    "water_heater": {"node", "converter", "storage"},
    "pv_forecast": {"source"},
    "pool_pump": {"sink"},
}


@pytest.mark.unit
@pytest.mark.req("003:FR-010")
def test_exported_manifest_schemas_include_primitives_metadata() -> None:
    # REQ: 003:FR-010
    """Manifest schemas expose additive primitive metadata for all device types."""
    schemas = get_all_schemas()
    manifest_schemas = {name: schema for name, schema in schemas.items() if name.startswith("manifest/")}

    assert manifest_schemas
    for schema_name, schema in manifest_schemas.items():
        primitives = schema.get("x-hemm-primitives")
        assert isinstance(primitives, list), schema_name
        assert primitives, schema_name
        assert all(isinstance(primitive, str) and primitive for primitive in primitives), schema_name

    for device_type, expected in EXPECTED_PRIMITIVES.items():
        schema = get_manifest_schema(device_type)
        primitives = schema.get("x-hemm-primitives")
        assert isinstance(primitives, list)
        assert expected <= set(primitives)
        assert schema == schemas[f"manifest/{device_type}"]


@pytest.mark.unit
@pytest.mark.req("003:FR-010")
def test_existing_ha_manifest_still_validates_without_primitives_field() -> None:
    # REQ: 003:FR-010
    """Existing ha-hemm manifests validate unchanged; primitive metadata is not required."""
    manifest = build_manifest(
        {
            "id": "pool_pump_1",
            CONF_DEVICE_TYPE: DeviceType.POOL_PUMP,
            CONF_DEVICE_NAME: "HEMM Pool Pump",
            CONF_MAX_POWER_KW: 1.2,
            CONF_SAFE_DEFAULT_SCRIPT: "script.hemm_pool_pump_safe",
        }
    )
    payload = manifest.model_dump(mode="json")

    assert "x-hemm-primitives" not in payload
    validated = validate_manifest(payload)
    assert validated.type.value == "pool_pump"
    assert validated.device_id == "pool_pump_1"
