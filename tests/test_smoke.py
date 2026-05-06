"""Smoke tests for the HEMM HA integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_domain_constant() -> None:
    """Verify the domain constant is correct."""
    from custom_components.hemm.const import DOMAIN

    assert DOMAIN == "hemm"


@pytest.mark.unit
def test_manifest_domain() -> None:
    """Verify manifest.json has correct domain."""
    manifest_path = Path(__file__).parent.parent / "custom_components" / "hemm" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "hemm"
    assert manifest["config_flow"] is True


@pytest.mark.unit
def test_manifest_has_required_fields() -> None:
    """Verify manifest.json has all required HACS fields."""
    manifest_path = Path(__file__).parent.parent / "custom_components" / "hemm" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    required_fields = ["domain", "name", "version", "documentation", "codeowners", "requirements"]
    for field in required_fields:
        assert field in manifest, f"Missing required field: {field}"


@pytest.mark.unit
def test_constants_consistency() -> None:
    """Verify constants are consistent."""
    from custom_components.hemm.const import (
        DEFAULT_SOLVER_BACKEND,
        PRICE_ADAPTERS,
        SOLVER_BACKENDS,
    )

    assert DEFAULT_SOLVER_BACKEND in SOLVER_BACKENDS
    assert len(PRICE_ADAPTERS) >= 1
    assert len(SOLVER_BACKENDS) == 2
