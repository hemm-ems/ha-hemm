"""CI guard: fail if the committed req-tag manifest drifts from the tests.

The `@pytest.mark.req("NNN:FR-MMM")` tags link this repo's tests to the spec FRs
in the parent `specs/` repo. The manifest is generated from the parent tool:
`python3 tools/req_coverage.py --write-tag-manifests`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REQ_REF = re.compile(r"(?:@pytest\.mark\.req\(|#\s*REQ:)\s*([^)\n]+)")
_REQ_ID = re.compile(r"\d{3}:FR-\d{3}")


@pytest.mark.unit
def test_req_tags_match_committed_manifest() -> None:
    tests_dir = Path(__file__).parent
    manifest = json.loads((tests_dir / "req_tags_manifest.json").read_text(encoding="utf-8"))
    expected = set(manifest["frs"])

    found: set[str] = set()
    for py in tests_dir.rglob("test_*.py"):
        if py.name == Path(__file__).name:
            continue
        for ref in _REQ_REF.finditer(py.read_text(encoding="utf-8")):
            found.update(_REQ_ID.findall(ref.group(1)))

    assert found == expected, (
        "Requirement tags differ from tests/req_tags_manifest.json. "
        "Run `python3 tools/req_coverage.py --write-tag-manifests` from the "
        "umbrella repo when adding/removing req tags. "
        f"Missing from tests: {sorted(expected - found)}; "
        f"missing from manifest: {sorted(found - expected)}"
    )
