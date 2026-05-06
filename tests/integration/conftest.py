"""Pytest fixtures for container-based integration tests."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio

from .hactl_client import HactlClient

_LOGGER = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker-compose.test.yml"
CONFIG_DIR = Path(__file__).parent / "config"


def pytest_collection_modifyitems(items: list) -> None:
    """Enable sockets for integration tests (pytest-socket blocks by default)."""
    for item in items:
        item.add_marker(pytest.mark.enable_socket)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations() -> None:
    """Override parent conftest — container tests don't use HA test framework."""


@pytest.fixture(scope="session")
def ha_version() -> str:
    """HA version from environment or default."""
    return os.environ.get("HA_VERSION", "stable")


@pytest.fixture(scope="session")
def ha_base_url() -> str:
    """HA container base URL."""
    return os.environ.get("HA_BASE_URL", "http://localhost:8123")


@pytest.fixture(scope="session")
def docker_compose_up(ha_version: str):
    """Start HA container via docker-compose for the test session."""
    # Skip if container is already running (e.g. manually started)
    skip_docker = os.environ.get("SKIP_DOCKER_COMPOSE", "")
    if skip_docker:
        yield
        return

    env = {**os.environ, "HA_VERSION": ha_version}

    # Ensure clean config dir (remove any stale .storage etc.)
    storage_dir = CONFIG_DIR / ".storage"
    if storage_dir.exists():
        shutil.rmtree(storage_dir)

    _LOGGER.info("Starting HA container (version=%s)...", ha_version)
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
        env=env,
        check=True,
        capture_output=True,
        timeout=180,
    )

    yield

    _LOGGER.info("Stopping HA container...")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        env=env,
        capture_output=True,
        timeout=60,
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ha_token(docker_compose_up: None, ha_base_url: str) -> str:
    """Perform onboarding and return a long-lived access token.

    Waits for HA to be ready, performs onboarding if needed, returns token.
    """
    async with HactlClient(base_url=ha_base_url) as client:
        ready = await client.wait_for_ready(timeout=120.0)
        assert ready, "HA container did not become ready within 120s"

        if await client.needs_onboarding():
            await client.complete_onboarding()
            _LOGGER.info("HA onboarding complete, token acquired")
        else:
            # Already onboarded (e.g., persistent volume) — use env token
            token = os.environ.get("HA_TOKEN", "")
            if not token:
                msg = "HA is already onboarded but no HA_TOKEN provided"
                raise RuntimeError(msg)
            client._token = token

        return client._token


@pytest_asyncio.fixture
async def ha_client(ha_token: str, ha_base_url: str) -> AsyncGenerator[HactlClient, None]:
    """Create a per-test authenticated hactl client."""
    async with HactlClient(base_url=ha_base_url, token=ha_token) as client:
        yield client
