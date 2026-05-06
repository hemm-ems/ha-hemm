"""Example tests with all four markers to verify marker filtering works."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_marker_unit() -> None:
    """This test runs in the default test suite."""
    assert True


@pytest.mark.container
def test_marker_container() -> None:
    """This test should NOT run in default suite (requires Docker)."""
    assert True


@pytest.mark.pi
def test_marker_pi() -> None:
    """This test should NOT run in default suite (requires Pi hardware)."""
    assert True


@pytest.mark.slow
def test_marker_slow() -> None:
    """This test should NOT run in default suite (long-running sim)."""
    assert True
