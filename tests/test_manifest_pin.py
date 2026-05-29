"""Manifest release pin guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
@pytest.mark.req("009:FR-002")
def test_manifest_pins_core_to_integration_version() -> None:
    manifest = json.loads((REPO_ROOT / "custom_components" / "hemm" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["requirements"] == ["hemm==2026.5.2"]
    assert manifest["version"] == "2026.5.2"
    assert manifest["requirements"] == [f"hemm=={manifest['version']}"]
