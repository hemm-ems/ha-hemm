"""Manifest release pin guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
@pytest.mark.req("009:FR-002")
def test_manifest_core_pin() -> None:
    # The core pin must reference a real, released `hemm` PyPI version — a
    # mismatch silently downgrades the container/HA core at entry setup. Bump
    # this literal deliberately, in lockstep with a core PyPI release.
    #
    # The integration's own `version` is decoupled from the pin: ha-hemm may
    # patch ahead of core (e.g. packaging/HACS fixes) without a core bump, so
    # `version` can be >= the pinned core version.
    manifest = json.loads((REPO_ROOT / "custom_components" / "hemm" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["requirements"] == ["hemm==2026.7.2"]
    assert manifest["version"] == "2026.7.2"
