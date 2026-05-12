"""Sim house runner — reads house YAML, drives hactl to provision a complete HA house.

Workflow:
1. Wait for HA healthy
2. Complete onboarding
3. Create HEMM hub via config flow
4. For each device in house.yaml: 3-step options flow (add_device → select_device → configure_device)
5. Verify entities exist
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.integration.hactl import Hactl, HactlError

_LOGGER = logging.getLogger(__name__)

# Onboarding constants (match integration conftest)
_CLIENT_ID = "https://hemm.test/"
_ONBOARD_NAME = "HEMM Sim Home"
_ONBOARD_USER = "hemm_test"
_ONBOARD_PASS = "hemm_test_pass_123"

HOUSES_DIR = Path(__file__).parent / "houses"


@dataclass
class HouseConfig:
    """Parsed house definition from YAML."""

    name: str
    description: str
    ha_port: int
    hub: dict[str, Any]
    devices: list[dict[str, Any]]
    constraints: list[dict[str, Any]] = field(default_factory=list)
    quirks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> HouseConfig:
        """Load house config from YAML file."""
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            description=data["description"],
            ha_port=data["ha_port"],
            hub=data["hub"],
            devices=data["devices"],
            constraints=data.get("constraints", []),
            quirks=data.get("quirks", []),
        )


def discover_houses() -> list[HouseConfig]:
    """Find all house.yaml files under houses/."""
    houses = []
    for house_dir in sorted(HOUSES_DIR.iterdir()):
        house_yaml = house_dir / "house.yaml"
        if house_yaml.exists():
            houses.append(HouseConfig.from_yaml(house_yaml))
    return houses


def discover_house_names() -> list[str]:
    """Return sorted list of house directory names."""
    return [d.name for d in sorted(HOUSES_DIR.iterdir()) if (d / "house.yaml").exists()]


# ---------------------------------------------------------------------------
# HA interaction (stdlib only — no aiohttp at setup time)
# ---------------------------------------------------------------------------


def _wait_for_ha(base_url: str, timeout: float = 180.0) -> None:
    """Poll HA until it responds (healthy or 401)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/api/")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as e:
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
    """Headless onboarding → long-lived access token.

    1. Create owner → auth_code
    2. Exchange → access_token
    3. Complete core_config + analytics
    4. WS → long_lived_access_token
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
    _LOGGER.info("[%s] Onboarding: owner created", base_url)

    # Step 2: Exchange auth code
    form_data = f"grant_type=authorization_code&code={auth_code}&client_id={_CLIENT_ID}".encode()
    req = urllib.request.Request(
        f"{base_url}/auth/token",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    access_token = data["access_token"]

    # Step 3: Complete remaining steps
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

    # Step 4: WS long-lived token
    import asyncio

    ll_token = asyncio.get_event_loop().run_until_complete(_create_long_lived_token(base_url, access_token))
    _LOGGER.info("[%s] Onboarding complete — token acquired", base_url)
    return ll_token


async def _create_long_lived_token(base_url: str, access_token: str) -> str:
    """Create long-lived token via HA WebSocket."""
    import aiohttp

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
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
                "client_name": "hemm-sim-test",
                "lifespan": 365,
            }
        )
        msg = await ws.receive_json()
        if not msg.get("success"):
            raise RuntimeError(f"Long-lived token creation failed: {msg}")
        return msg["result"]


# ---------------------------------------------------------------------------
# Setup engine
# ---------------------------------------------------------------------------


def _get_hemm_entry_id(hactl: Hactl) -> str | None:
    """Get the hemm config entry ID, or None."""
    result = hactl.config_entries()
    entries = result.json_data if isinstance(result.json_data, list) else result.json_data.get("entries", [])
    hemm_entries = [e for e in entries if e.get("domain") == "hemm"]
    return hemm_entries[0]["entry_id"] if hemm_entries else None


def _create_hemm_hub(hactl: Hactl, hub_config: dict[str, Any]) -> str:
    """Create the HEMM hub config entry. Returns entry_id."""
    existing = _get_hemm_entry_id(hactl)
    if existing:
        _LOGGER.info("HEMM hub already exists (entry_id=%s)", existing)
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

    entry_id = _get_hemm_entry_id(hactl)
    if not entry_id:
        raise RuntimeError(f"Failed to create HEMM hub: {result.json_data}")
    _LOGGER.info("HEMM hub created (entry_id=%s)", entry_id)
    return entry_id


def _add_device(hactl: Hactl, entry_id: str, device: dict[str, Any]) -> None:
    """Add a single device via the 3-step options flow.

    Steps:
    1. Start options flow → send {"action": "add_device"}
    2. Send {"device_type": "<type>", "tier": "<tier>"}
    3. Send device config dict (including safe_default_script)
    """
    device_name = device["config"]["device_name"]
    device_type = device["type"]
    tier = device.get("tier", "beginner")

    _LOGGER.info("Adding device: %s (type=%s, tier=%s)", device_name, device_type, tier)

    # Step 1: Start options flow and select "add_device"
    result = hactl.config_options(entry_id)
    flow_id = result.json_data["flow_id"]

    result = hactl.config_flow_step(flow_id, {"action": "add_device"}, options=True)
    if result.json_data.get("step_id") != "select_device":
        raise RuntimeError(f"Expected select_device step, got: {result.json_data}")

    # Step 2: Select device type and tier
    result = hactl.config_flow_step(
        flow_id,
        {"device_type": device_type, "tier": tier},
        options=True,
    )
    if result.json_data.get("step_id") != "configure_device":
        raise RuntimeError(f"Expected configure_device step, got: {result.json_data}")

    # Step 3: Submit device configuration
    config_data = dict(device["config"])
    config_data["safe_default_script"] = device["safe_default_script"]

    # Include control_class if specified (non-default)
    if "control_class" in device:
        config_data["control_class"] = device["control_class"]

    result = hactl.config_flow_step(flow_id, config_data, options=True)
    if result.json_data.get("type") != "create_entry":
        raise RuntimeError(f"Device creation failed for {device_name}: {result.json_data}")

    _LOGGER.info("Device added: %s ✓", device_name)


def setup_house(house: HouseConfig, hactl: Hactl) -> None:
    """Provision a complete house: hub + all devices.

    Call this after HA is healthy + onboarded and hactl is configured.
    """
    _LOGGER.info("Setting up house: %s (%s)", house.name, house.description)

    # Create HEMM hub
    entry_id = _create_hemm_hub(hactl, house.hub)

    # Add each device
    for device in house.devices:
        _add_device(hactl, entry_id, device)

    _LOGGER.info("House %s setup complete — %d devices configured", house.name, len(house.devices))


def verify_house(house: HouseConfig, hactl: Hactl) -> dict[str, Any]:
    """Verify house setup: check entities exist and hub is loaded.

    Returns a summary dict with verification results.
    """
    results: dict[str, Any] = {"house": house.name, "devices": {}, "hub_loaded": False}

    # Check hub is loaded
    entry_id = _get_hemm_entry_id(hactl)
    if entry_id:
        entries_result = hactl.config_entries()
        entries = (
            entries_result.json_data
            if isinstance(entries_result.json_data, list)
            else entries_result.json_data.get("entries", [])
        )
        hemm_entry = next((e for e in entries if e.get("domain") == "hemm"), None)
        results["hub_loaded"] = hemm_entry is not None and hemm_entry.get("state") == "loaded"

    # Check entities per device
    for device in house.devices:
        device_name = device["config"]["device_name"]
        try:
            ent_result = hactl.ent_ls(pattern="*hemm*")
            results["devices"][device_name] = {
                "entities_found": ent_result.success,
            }
        except HactlError:
            results["devices"][device_name] = {"entities_found": False}

    return results


def full_setup(house: HouseConfig, base_url: str, hactl_binary: Path, bin_dir: Path) -> Hactl:
    """End-to-end: wait → onboard → create hactl → setup house → verify.

    Returns the configured Hactl instance for further use.
    """
    import tempfile

    _LOGGER.info("=== Full setup for house: %s (port %d) ===", house.name, house.ha_port)

    # Wait for HA
    _wait_for_ha(base_url)

    # Onboard if needed
    token_file = bin_dir / f".ha_sim_token_{house.name}"
    if _needs_onboarding(base_url):
        token = _complete_onboarding(base_url)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token)
    else:
        if token_file.exists():
            token = token_file.read_text().strip()
        else:
            raise RuntimeError(f"HA already onboarded for {house.name} but no cached token")

    # Create hactl instance pointing at this house's HA
    hactl_dir = Path(tempfile.mkdtemp(prefix=f"hactl_sim_{house.name}_"))
    env_file = hactl_dir / ".env"
    env_file.write_text(f"HA_URL={base_url}\nHA_TOKEN={token}\n")
    hactl = Hactl(binary=hactl_binary, instance_dir=hactl_dir, timeout=60)

    # Wait for hactl to connect
    hactl.health()

    # Setup house
    setup_house(house, hactl)

    # Verify
    results = verify_house(house, hactl)
    _LOGGER.info("Verification: %s", json.dumps(results, indent=2))

    return hactl
