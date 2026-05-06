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
def test_hemm_core_importable() -> None:
    """Verify the hemm core library is installed (editable install)."""
    from importlib.metadata import version

    v = version("hemm")
    assert v == "0.1.0"
