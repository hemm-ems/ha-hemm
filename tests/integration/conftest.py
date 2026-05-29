"""Pytest fixtures for container-based integration tests."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from .hactl import Hactl, get_hactl_binary_name, get_hactl_download_url

_LOGGER = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker-compose.test.yml"
CONFIG_DIR = Path(__file__).parent / "config"
BIN_DIR = Path(__file__).parent.parent.parent / ".bin"

# Onboarding constants
_CLIENT_ID = "https://hemm.test/"
_ONBOARD_NAME = "HEMM Test"
_ONBOARD_USER = "hemm_test"
_ONBOARD_PASS = "hemm_test_pass_123"
_COMPANION_TOKEN = "integration-test-token-12345"


def pytest_collection_modifyitems(items: list) -> None:
    """Enable sockets for integration tests (pytest-socket blocks by default)."""
    try:
        import pytest_socket

        pytest_socket.enable_socket()
        pytest_socket.disable_socket = lambda *a, **kw: None
        pytest_socket.socket_allow_hosts = lambda *a, **kw: None
    except (ImportError, AttributeError):
        pass


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Force-enable sockets before each integration test."""
    try:
        import pytest_socket

        pytest_socket.enable_socket()
    except (ImportError, AttributeError):
        pass


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
def hactl_binary() -> Path:
    """Ensure hactl binary is available. Download from GitHub releases if needed.

    Checks in order:
    1. .bin/hactl (project-local, from `make install-hactl`)
    2. PATH (system-wide install)
    3. Download latest release to .bin/
    """
    binary_name = get_hactl_binary_name()

    # Check project .bin/ directory
    local_bin = BIN_DIR / binary_name
    if local_bin.exists():
        _LOGGER.info("Using hactl from .bin/: %s", local_bin)
        return local_bin

    # Check PATH
    which_result = shutil.which("hactl")
    if which_result:
        _LOGGER.info("Using hactl from PATH: %s", which_result)
        return Path(which_result)

    # Download latest release
    _LOGGER.info("Downloading hactl binary from GitHub releases...")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    url = get_hactl_download_url()

    try:
        import tarfile
        import tempfile
        import zipfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(url).suffix) as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            archive_path = Path(tmp.name)

        if url.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(BIN_DIR)
        elif url.endswith(".tar.gz"):
            with tarfile.open(archive_path) as tf:
                tf.extractall(BIN_DIR)

        archive_path.unlink(missing_ok=True)

        # Make executable on Unix
        if sys.platform != "win32":
            local_bin.chmod(local_bin.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        _LOGGER.info("Downloaded hactl to: %s", local_bin)
        return local_bin
    except Exception as e:
        pytest.skip(f"Failed to download hactl binary: {e}")


@pytest.fixture(scope="session")
def docker_compose_up(ha_version: str):
    """Start HA container via docker-compose for the test session.

    After HA is healthy, installs hemm core and the hactl-companion
    inside the HA container, restarts HA, then starts the companion
    as a background process.
    """
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

    # Install hemm core and companion inside HA container
    _LOGGER.info("Installing hemm + companion inside container...")
    subprocess.run(
        ["docker", "exec", "hemm-ha-test", "pip", "install", "--quiet", "/hemm-src"],
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "hemm-ha-test",
            "pip",
            "install",
            "--quiet",
            "git+https://github.com/hemm-ems/hactl-companion.git",
        ],
        capture_output=True,
        timeout=120,
    )

    # Restart HA so it picks up the newly installed hemm package
    _LOGGER.info("Restarting HA container to load hemm...")
    subprocess.run(["docker", "restart", "hemm-ha-test"], capture_output=True, timeout=60)
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
        env=env,
        capture_output=True,
        timeout=180,
    )

    # Start companion as a background process inside HA
    _LOGGER.info("Starting companion inside HA container...")
    subprocess.run(
        [
            "docker",
            "exec",
            "-d",
            "hemm-ha-test",
            "sh",
            "-c",
            f"SUPERVISOR_TOKEN={_COMPANION_TOKEN} python3 -m companion",
        ],
        capture_output=True,
        timeout=30,
    )

    # Wait for companion to be healthy
    _wait_for_companion("http://127.0.0.1:9100", timeout=30)

    yield

    _LOGGER.info("Stopping HA container...")
    token_file = BIN_DIR / ".ha_test_token"
    token_file.unlink(missing_ok=True)
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        env=env,
        capture_output=True,
        timeout=60,
    )


def _wait_for_companion(base_url: str, timeout: int = 30) -> None:
    """Poll companion /v1/health until it responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/v1/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    _LOGGER.info("Companion is healthy")
                    return
        except Exception:
            pass
        time.sleep(1)
    _LOGGER.warning("Companion did not become healthy within %ds — tests may skip", timeout)


# --- Onboarding (stdlib urllib, no external client) ---


def _wait_for_ha(base_url: str, timeout: float = 120.0) -> None:
    """Poll HA until it responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/api/")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as e:
            # 401 means HA is running but we're unauthenticated — that's fine
            if e.code == 401:
                return
        except Exception:
            pass
        time.sleep(2)
    msg = f"HA not ready at {base_url} within {timeout}s"
    raise RuntimeError(msg)


def _needs_onboarding(base_url: str) -> bool:
    """Check if HA still needs onboarding."""
    req = urllib.request.Request(f"{base_url}/api/onboarding")
    with urllib.request.urlopen(req, timeout=10) as resp:
        steps = json.loads(resp.read())
        return any(s.get("step") == "user" and not s.get("done") for s in steps)


def _complete_onboarding(base_url: str) -> str:
    """Run headless onboarding and return a long-lived access token.

    1. Create owner user → auth_code
    2. Exchange auth_code → access_token
    3. Complete core_config + analytics steps
    4. Create long-lived token via WebSocket
    """
    # Step 1: Create owner
    body = json.dumps(
        {
            "client_id": _CLIENT_ID,
            "name": _ONBOARD_NAME,
            "username": _ONBOARD_USER,
            "password": _ONBOARD_PASS,
            "language": "en",
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/api/onboarding/users",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    auth_code = data["auth_code"]
    _LOGGER.info("Onboarding: owner created")

    # Step 2: Exchange auth code for access token
    form_data = (f"grant_type=authorization_code&code={auth_code}&client_id={_CLIENT_ID}").encode()
    req = urllib.request.Request(
        f"{base_url}/auth/token",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    access_token = data["access_token"]
    _LOGGER.info("Onboarding: auth code exchanged")

    # Step 3: Complete remaining onboarding steps
    for step in ("core_config", "analytics"):
        req = urllib.request.Request(
            f"{base_url}/api/onboarding/{step}",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        with contextlib.suppress(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=30)

    # Step 4: Create long-lived token via WebSocket
    import asyncio

    ll_token = asyncio.get_event_loop().run_until_complete(_create_long_lived_token(base_url, access_token))
    _LOGGER.info("Onboarding: long-lived token created")
    return ll_token


async def _create_long_lived_token(base_url: str, access_token: str) -> str:
    """Create a long-lived token via the HA WebSocket API."""
    import aiohttp

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/api/websocket"

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as session, session.ws_connect(ws_url) as ws:
        # Read auth_required
        msg = await ws.receive_json()
        assert msg.get("type") == "auth_required"

        # Send auth
        await ws.send_json({"type": "auth", "access_token": access_token})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            err_msg = f"WS auth failed: {msg}"
            raise RuntimeError(err_msg)

        # Request long-lived token
        await ws.send_json(
            {
                "id": 1,
                "type": "auth/long_lived_access_token",
                "client_name": "hemm-container-test",
                "lifespan": 365,
            }
        )
        msg = await ws.receive_json()
        if not msg.get("success"):
            err_msg = f"Long-lived token creation failed: {msg}"
            raise RuntimeError(err_msg)
        return msg["result"]


@pytest.fixture(scope="session")
def ha_token(docker_compose_up: None, ha_base_url: str) -> str:
    """Perform onboarding and return a long-lived access token.

    Waits for HA to be ready, performs onboarding if needed, returns token.
    Caches token to .bin/.ha_test_token for reuse with SKIP_DOCKER_COMPOSE.
    """
    token_file = BIN_DIR / ".ha_test_token"

    _wait_for_ha(ha_base_url)

    if _needs_onboarding(ha_base_url):
        token = _complete_onboarding(ha_base_url)
        _LOGGER.info("HA onboarding complete, token acquired")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token)
    else:
        token = os.environ.get("HA_TOKEN", "")
        if not token and token_file.exists():
            token = token_file.read_text().strip()
            _LOGGER.info("Using cached token from %s", token_file)
        if not token:
            msg = "HA is already onboarded but no HA_TOKEN provided and no cached token"
            raise RuntimeError(msg)

    return token


# --- hactl binary fixtures ---


@pytest.fixture(scope="session")
def hactl_dir(ha_token: str, ha_base_url: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a temporary hactl instance directory with .env pointing at the test HA.

    Includes COMPANION_URL so hactl auto-discovers the companion running inside HA.
    """
    dir_path = tmp_path_factory.mktemp("hactl_instance")
    env_file = dir_path / ".env"
    env_file.write_text(f"HA_URL={ha_base_url}\nHA_TOKEN={ha_token}\nCOMPANION_URL=http://127.0.0.1:9100\n")
    _LOGGER.info("Created hactl instance dir: %s", dir_path)
    return dir_path


@pytest.fixture(scope="session")
def hactl_session(hactl_binary: Path, hactl_dir: Path) -> Hactl:
    """Session-scoped hactl instance — reused across tests for efficiency."""
    h = Hactl(binary=hactl_binary, instance_dir=hactl_dir)
    try:
        h.health()
        _LOGGER.info("hactl session connected to HA successfully")
    except Exception as e:
        pytest.skip(f"hactl cannot connect to HA container: {e}")
    return h


@pytest.fixture
def hactl(hactl_binary: Path, hactl_dir: Path) -> Hactl:
    """Function-scoped hactl instance — fresh per test."""
    return Hactl(binary=hactl_binary, instance_dir=hactl_dir)
