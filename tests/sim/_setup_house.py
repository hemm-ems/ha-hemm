"""One-shot script: onboard HA + get long-lived token + provision house via hactl.

Usage:
    uv run python tests/sim/_setup_house.py <house_name>
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOG = logging.getLogger(__name__)

BIN_DIR = Path(__file__).parent.parent.parent / ".bin"
HOUSES_DIR = Path(__file__).parent / "houses"

CLIENT_ID = "https://hemm.test/"
ONBOARD_USER = "hemm_test"
ONBOARD_PASS = "hemm_test_pass_123"

# Port mapping
HOUSE_PORTS = {
    "starter": 8130,
    "family": 8131,
    "comfort": 8132,
    "villa": 8133,
    "para14a": 8134,
}


def wait_for_ha(base_url: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/api/")
            with urllib.request.urlopen(req, timeout=5):
                _LOG.info("HA is ready at %s", base_url)
                return
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _LOG.info("HA is ready at %s (401 = authenticated)", base_url)
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"HA not ready at {base_url} within {timeout}s")


def needs_onboarding(base_url: str) -> bool:
    req = urllib.request.Request(f"{base_url}/api/onboarding")
    with urllib.request.urlopen(req, timeout=10) as resp:
        steps = json.loads(resp.read())
        return any(s.get("step") == "user" and not s.get("done") for s in steps)


def do_onboarding(base_url: str) -> str:
    """Create owner user and return access_token."""
    body = json.dumps(
        {
            "client_id": CLIENT_ID,
            "name": "HEMM Sim",
            "username": ONBOARD_USER,
            "password": ONBOARD_PASS,
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
    _LOG.info("Owner created")

    form_data = f"grant_type=authorization_code&code={auth_code}&client_id={CLIENT_ID}".encode()
    req = urllib.request.Request(
        f"{base_url}/auth/token",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    access_token = data["access_token"]
    _LOG.info("Access token acquired via onboarding")

    for step in ("core_config", "analytics", "integration"):
        req = urllib.request.Request(
            f"{base_url}/api/onboarding/{step}",
            data=b"{}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
        with contextlib.suppress(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=30)
    _LOG.info("All onboarding steps completed")

    return access_token


def login_existing(base_url: str) -> str:
    """Login with existing user credentials and return access_token."""
    body = json.dumps(
        {
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": f"{base_url}/",
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/auth/login_flow",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    flow_id = data["flow_id"]

    body = json.dumps(
        {
            "username": ONBOARD_USER,
            "password": ONBOARD_PASS,
            "client_id": CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/auth/login_flow/{flow_id}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("type") != "create_entry":
        raise RuntimeError(f"Login failed: {data}")
    auth_code = data["result"]

    form_data = f"grant_type=authorization_code&code={auth_code}&client_id={CLIENT_ID}".encode()
    req = urllib.request.Request(
        f"{base_url}/auth/token",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    _LOG.info("Access token acquired via login")
    return data["access_token"]


async def create_ll_token(base_url: str, access_token: str, house_name: str) -> str:
    import aiohttp

    ws_url = base_url.replace("http://", "ws://") + "/api/websocket"
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as session, session.ws_connect(ws_url) as ws:
        msg = await ws.receive_json()
        assert msg.get("type") == "auth_required"
        await ws.send_json({"type": "auth", "access_token": access_token})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"WS auth failed: {msg}")
        await ws.send_json(
            {
                "id": 1,
                "type": "auth/long_lived_access_token",
                "client_name": f"hemm-sim-{house_name}",
                "lifespan": 365,
            }
        )
        msg = await ws.receive_json()
        if not msg.get("success"):
            raise RuntimeError(f"Token creation failed: {msg}")
        return msg["result"]


def get_token(base_url: str, house_name: str) -> str:
    """Get or create a long-lived HA token for this house."""
    token_file = BIN_DIR / f".ha_sim_token_{house_name}"

    if token_file.exists():
        token = token_file.read_text().strip()
        # Verify token works
        try:
            req = urllib.request.Request(
                f"{base_url}/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10):
                _LOG.info("Cached token valid")
                return token
        except Exception:
            _LOG.info("Cached token invalid, re-authenticating")

    access_token = do_onboarding(base_url) if needs_onboarding(base_url) else login_existing(base_url)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    ll_token = asyncio.run(create_ll_token(base_url, access_token, house_name))

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(ll_token)
    _LOG.info("Long-lived token saved to %s", token_file)
    return ll_token


def setup_hactl(base_url: str, token: str, house_name: str):  # -> Hactl
    """Create a hactl instance pointing at the house's HA."""
    import tempfile

    # Add project root to path so we can import
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from tests.integration.hactl import Hactl

    hactl_dir = Path(tempfile.mkdtemp(prefix=f"hactl_sim_{house_name}_"))
    env_file = hactl_dir / ".env"
    env_file.write_text(f"HA_URL={base_url}\nHA_TOKEN={token}\n")
    hactl = Hactl(
        binary=str(BIN_DIR / ("hactl.exe" if sys.platform == "win32" else "hactl")), instance_dir=hactl_dir, timeout=60
    )
    hactl.health()
    _LOG.info("hactl connected to HA")
    return hactl


def get_hemm_entry_id(hactl) -> str | None:
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    return hemm_entries[0]["entry_id"] if hemm_entries else None


def create_hemm_hub(hactl, hub_config: dict) -> str:
    existing = get_hemm_entry_id(hactl)
    if existing:
        _LOG.info("HEMM hub already exists (entry_id=%s)", existing)
        return existing

    result = hactl.config_flow_start("hemm")
    flow_id = result.json_data["flow_id"]
    flow_data = {
        "name": "HEMM",
        "horizon_hours": hub_config.get("horizon_hours", 24),
        "max_iterations": hub_config.get("max_iterations", 50),
        "price_adapter": hub_config.get("price_adapter", "template"),
        "solver_backend": hub_config.get("solver_backend", "milp_central"),
    }
    result = hactl.config_flow_step(flow_id, flow_data)
    entry_id = get_hemm_entry_id(hactl)
    if not entry_id:
        raise RuntimeError(f"Failed to create HEMM hub: {result.json_data}")
    _LOG.info("HEMM hub created (entry_id=%s)", entry_id)
    return entry_id


def add_device(hactl, entry_id: str, device: dict) -> None:
    device_name = device["config"]["device_name"]
    device_type = device["type"]
    tier = device.get("tier", "beginner")

    _LOG.info("Adding device: %s (type=%s)", device_name, device_type)

    # Step 1: Start options flow
    result = hactl.config_options(entry_id)
    flow_id = result.json_data["flow_id"]

    # Step 2: Select add_device action
    result = hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
    if result.json_data.get("step_id") != "select_device":
        raise RuntimeError(f"Expected select_device, got: {result.json_data}")

    # Step 3: Select device type
    result = hactl.config_flow_step(flow_id, {"device_type": device_type, "tier": tier}, options=True)
    if result.json_data.get("step_id") != "configure_device":
        raise RuntimeError(f"Expected configure_device, got: {result.json_data}")

    # Step 4: Submit device config
    config_data = dict(device["config"])
    config_data["safe_default_script"] = device["safe_default_script"]
    if "control_class" in device:
        config_data["control_class"] = device["control_class"]

    result = hactl.config_flow_step(flow_id, config_data, options=True)
    if result.json_data.get("type") != "create_entry":
        raise RuntimeError(f"Device creation failed for {device_name}: {result.json_data}")
    _LOG.info("Device added: %s", device_name)


def main():
    import yaml

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <house_name>")
        print(f"Available houses: {', '.join(sorted(HOUSE_PORTS.keys()))}")
        sys.exit(1)

    house_name = sys.argv[1]
    if house_name not in HOUSE_PORTS:
        print(f"Unknown house: {house_name}. Available: {', '.join(sorted(HOUSE_PORTS.keys()))}")
        sys.exit(1)

    port = HOUSE_PORTS[house_name]
    base_url = f"http://localhost:{port}"

    # Load house config
    house_yaml = HOUSES_DIR / house_name / "house.yaml"
    if not house_yaml.exists():
        print(f"House YAML not found: {house_yaml}")
        sys.exit(1)
    house = yaml.safe_load(house_yaml.read_text(encoding="utf-8"))
    _LOG.info("=== Setting up house: %s (%s) ===", house["name"], house["description"])

    # Wait for HA
    wait_for_ha(base_url)

    # Get token
    token = get_token(base_url, house_name)
    _LOG.info("Token ready")

    # Setup hactl
    hactl = setup_hactl(base_url, token, house_name)

    # Create HEMM hub
    entry_id = create_hemm_hub(hactl, house["hub"])

    # Add devices
    for device in house["devices"]:
        add_device(hactl, entry_id, device)

    _LOG.info("=== House %s setup complete — %d devices ===", house["name"], len(house["devices"]))

    # Verify
    result = hactl.ent_ls(pattern="*hemm*")
    if result.success:
        entities = result.json_data if isinstance(result.json_data, list) else []
        _LOG.info("HEMM entities: %d", len(entities))
    else:
        _LOG.warning("Could not list entities")

    # Check health
    result = hactl.health()
    _LOG.info("HA Health: %s", json.dumps(result.json_data, indent=2) if result.json_data else "OK")


if __name__ == "__main__":
    main()
