"""Pytest fixtures for sim house tests.

Each house gets its own Docker container, HA onboarding, and hactl instance.
Houses run sequentially (one at a time) — parallel execution is out of scope
for the initial milestone.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.integration.hactl import Hactl, get_hactl_binary_name

from .runner import (
    HOUSES_DIR,
    HouseConfig,
    _complete_onboarding,
    _needs_onboarding,
    _wait_for_ha,
    setup_house,
)

_LOGGER = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).parent / "docker-compose.sim.yml"
BIN_DIR = Path(__file__).parent.parent.parent / ".bin"
_COMPANION_TOKEN = "sim-test-token-12345"


def pytest_collection_modifyitems(items: list) -> None:
    """Enable sockets for sim tests."""
    try:
        import pytest_socket

        pytest_socket.enable_socket()
        pytest_socket.disable_socket = lambda *a, **kw: None
        pytest_socket.socket_allow_hosts = lambda *a, **kw: None
    except (ImportError, AttributeError):
        pass


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Force-enable sockets before each sim test."""
    try:
        import pytest_socket

        pytest_socket.enable_socket()
    except (ImportError, AttributeError):
        pass


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations() -> None:
    """Override parent conftest — sim tests don't use HA test framework."""


@pytest.fixture(scope="session")
def hactl_binary() -> Path:
    """Ensure hactl binary is available (same logic as integration tests)."""
    binary_name = get_hactl_binary_name()
    local_bin = BIN_DIR / binary_name
    if local_bin.exists():
        return local_bin
    which_result = shutil.which("hactl")
    if which_result:
        return Path(which_result)
    pytest.skip("hactl binary not found — run `make install-hactl`")


@pytest.fixture(scope="session")
def bin_dir() -> Path:
    """Project .bin directory for caching tokens etc."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    return BIN_DIR


def _start_house_container(house: HouseConfig) -> None:
    """Start a single house container via docker compose."""
    env = {
        **os.environ,
        "HOUSE_NAME": house.name,
        "HOUSE_PORT": str(house.ha_port),
        "COMPANION_PORT": str(house.companion_port),
    }

    _LOGGER.info("Starting container for house: %s (port %d)", house.name, house.ha_port)
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
        env=env,
        check=True,
        capture_output=True,
        timeout=180,
    )

    # Install hemm core + companion inside container
    container_name = f"hemm-sim-{house.name}"
    _LOGGER.info("Installing hemm + companion inside %s...", container_name)
    subprocess.run(
        ["docker", "exec", container_name, "pip", "install", "--quiet", "/hemm-src"],
        check=True,
        capture_output=True,
        timeout=300,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "pip",
            "install",
            "--quiet",
            "git+https://github.com/swifty99/hactl_companion.git",
        ],
        capture_output=True,
        timeout=300,
    )

    # Restart HA to pick up hemm
    _LOGGER.info("Restarting %s to load hemm...", container_name)
    subprocess.run(["docker", "restart", container_name], capture_output=True, timeout=60)

    # Wait for healthy again
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
        env={
            **os.environ,
            "HOUSE_NAME": house.name,
            "HOUSE_PORT": str(house.ha_port),
            "COMPANION_PORT": str(house.companion_port),
        },
        check=True,
        capture_output=True,
        timeout=300,
    )

    # Start companion as background process inside container
    _LOGGER.info("Starting companion inside %s...", container_name)
    subprocess.run(
        [
            "docker",
            "exec",
            "-d",
            container_name,
            "sh",
            "-c",
            f"SUPERVISOR_TOKEN={_COMPANION_TOKEN} python3 -m companion",
        ],
        capture_output=True,
        timeout=30,
    )


def _stop_house_container(house: HouseConfig) -> None:
    """Stop and remove a house container."""
    env = {
        **os.environ,
        "HOUSE_NAME": house.name,
        "HOUSE_PORT": str(house.ha_port),
        "COMPANION_PORT": str(house.companion_port),
    }
    _LOGGER.info("Stopping container for house: %s", house.name)
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        env=env,
        capture_output=True,
        timeout=60,
    )
    # Clean up cached token
    token_file = BIN_DIR / f".ha_sim_token_{house.name}"
    token_file.unlink(missing_ok=True)


@pytest.fixture
def sim_house(request: pytest.FixtureRequest, hactl_binary: Path, bin_dir: Path):
    """Fixture that yields (HouseConfig, Hactl) for a parametrized house.

    Starts the container, onboards HA, provisions devices, then yields.
    Tears down the container after the test.
    """
    house_name = request.param
    house_yaml = HOUSES_DIR / house_name / "house.yaml"
    if not house_yaml.exists():
        pytest.skip(f"House definition not found: {house_yaml}")

    house = HouseConfig.from_yaml(house_yaml)
    base_url = f"http://localhost:{house.ha_port}"

    # Start container
    _start_house_container(house)

    try:
        import tempfile

        # Wait and onboard
        _wait_for_ha(base_url)

        token_file = bin_dir / f".ha_sim_token_{house.name}"
        if _needs_onboarding(base_url):
            token = _complete_onboarding(base_url)
            token_file.write_text(token)
        else:
            if token_file.exists():
                token = token_file.read_text().strip()
            else:
                raise RuntimeError(f"HA already onboarded for {house.name} but no token")

        # Create hactl instance
        hactl_dir = Path(tempfile.mkdtemp(prefix=f"hactl_sim_{house.name}_"))
        env_file = hactl_dir / ".env"
        companion_url = f"http://127.0.0.1:{house.companion_port}"
        env_file.write_text(f"HA_URL={base_url}\nHA_TOKEN={token}\nCOMPANION_URL={companion_url}\n")
        hactl = Hactl(binary=hactl_binary, instance_dir=hactl_dir, timeout=60)

        # Wait for hactl
        hactl.health()

        # Setup house
        setup_house(house, hactl)

        yield house, hactl

    finally:
        _stop_house_container(house)
