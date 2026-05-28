"""CI guard: fail if any expected requirement tag disappears from the test tree.

The `@pytest.mark.req("NNN:FR-MMM")` tags link this repo's tests to the spec FRs
in the parent `specs/` repo. They have been silently dropped during past rebases
and package renames, tearing out the SR→FR→test coverage linkage. This test makes
that loud: if a tag goes missing, CI fails here.

If a test is removed on purpose, update EXPECTED_FRS below in the same change (and
flip the FR's status in its spec, since it is no longer test-backed).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# FR IDs this repo's tests must keep covering. Update deliberately, never to
# silence a real regression.
EXPECTED_FRS = {
    "001:FR-001", "001:FR-002", "001:FR-003", "001:FR-004", "001:FR-005", "001:FR-012",
    "002:FR-001", "002:FR-002", "002:FR-007",
    "004:FR-002", "004:FR-003", "004:FR-004",
    "006:FR-001", "006:FR-002",
    "007:FR-001", "007:FR-002", "007:FR-003", "007:FR-004", "007:FR-005", "007:FR-006", "007:FR-007",
    "008:FR-001", "008:FR-002", "008:FR-003", "008:FR-006", "008:FR-007", "008:FR-008", "008:FR-010",
}

_REQ_REF = re.compile(r"(?:@pytest\.mark\.req\(|#\s*REQ:)\s*([^)\n]+)")
_REQ_ID = re.compile(r"\d{3}:FR-\d{3}")


@pytest.mark.unit
def test_expected_req_tags_present() -> None:
    tests_dir = Path(__file__).parent
    found: set[str] = set()
    for py in tests_dir.rglob("test_*.py"):
        if py.name == Path(__file__).name:
            continue
        for ref in _REQ_REF.finditer(py.read_text(encoding="utf-8")):
            found.update(_REQ_ID.findall(ref.group(1)))

    missing = EXPECTED_FRS - found
    assert not missing, (
        f"Requirement tags lost for {sorted(missing)}. If a test was removed on "
        f"purpose, update EXPECTED_FRS here and the FR's status in its spec."
    )
