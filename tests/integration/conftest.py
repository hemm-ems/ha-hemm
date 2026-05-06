"""Pytest fixtures for container-based integration tests."""

from __future__ import annotations

import os

import pytest

from .hactl_client import HactlClient


@pytest.fixture
def ha_base_url() -> str:
    """HA container base URL."""
    return os.environ.get("HA_BASE_URL", "http://localhost:8123")


@pytest.fixture
def ha_token() -> str:
    """HA long-lived access token."""
    return os.environ.get("HA_TOKEN", "")


@pytest.fixture
async def ha_client(ha_base_url: str, ha_token: str) -> HactlClient:
    """Create an hactl client connected to the HA container."""
    async with HactlClient(base_url=ha_base_url, token=ha_token) as client:
        yield client
