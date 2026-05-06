"""Container-based integration tests for HEMM."""

from __future__ import annotations

import pytest


@pytest.mark.container
def test_container_placeholder() -> None:
    """Placeholder for container tests — requires Docker setup."""
    # This test only runs with: make test-container
    # Real implementation comes when Docker compose is active
    pytest.skip("Container tests require Docker — run with 'make test-container'")
