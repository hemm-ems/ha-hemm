"""Hactl client — REST + WebSocket client for interacting with HA container.

Mirrors the hactl Go implementation's onboarding and API patterns.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "https://hemm.test/"
ONBOARD_NAME = "HEMM Test"
ONBOARD_USER = "hemm_test"
ONBOARD_PASS = "hemm_test_pass_123"


@dataclass
class HactlResult:
    """Result from an hactl API call."""

    status: int
    data: dict[str, Any] = field(default_factory=dict)


class HactlClient:
    """Client for interacting with HA container via REST API."""

    def __init__(self, base_url: str = "http://localhost:8123", token: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> HactlClient:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        connector = aiohttp.TCPConnector(use_dns_cache=True, resolver=aiohttp.ThreadedResolver())
        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._session:
            await self._session.close()

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def wait_for_ready(self, timeout: float = 120.0) -> bool:
        """Wait for HA to respond to API calls."""
        assert self._session is not None
        end_time = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end_time:
            try:
                async with self._session.get(self._url("/api/")) as resp:
                    if resp.status in (200, 401):
                        return True
            except (aiohttp.ClientError, OSError):
                pass
            await asyncio.sleep(2.0)
        return False

    async def needs_onboarding(self) -> bool:
        """Check if HA needs onboarding."""
        assert self._session is not None
        async with self._session.get(self._url("/api/onboarding")) as resp:
            if resp.status == 200:
                steps = await resp.json()
                return any(s.get("step") == "user" and not s.get("done") for s in steps)
            return False

    async def complete_onboarding(self) -> str:
        """Complete HA onboarding and return a long-lived access token.

        Mirrors hactl's headless onboarding flow:
        1. Create owner user → get auth_code
        2. Exchange auth_code → get access_token
        3. Complete core_config + analytics steps
        4. Create long-lived token via WebSocket
        """
        assert self._session is not None

        # Step 1: Create owner
        auth_code = await self._create_owner()
        _LOGGER.info("Onboarding: owner created, got auth_code")

        # Step 2: Exchange auth code for access token
        access_token = await self._exchange_auth_code(auth_code)
        _LOGGER.info("Onboarding: auth code exchanged for access token")

        # Step 3: Complete remaining onboarding steps
        authed_headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        async with self._session.post(
            self._url("/api/onboarding/core_config"), headers=authed_headers, json={}
        ) as resp:
            _LOGGER.debug("core_config step: %d", resp.status)

        async with self._session.post(self._url("/api/onboarding/analytics"), headers=authed_headers, json={}) as resp:
            _LOGGER.debug("analytics step: %d", resp.status)

        # Step 4: Create long-lived token via WebSocket
        ll_token = await self._create_long_lived_token(access_token)
        _LOGGER.info("Onboarding: long-lived token created")

        # Update session with the new token
        self._token = ll_token
        if self._session:
            await self._session.close()
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {ll_token}", "Content-Type": "application/json"},
            connector=connector,
        )

        return ll_token

    async def _create_owner(self) -> str:
        """Create the initial owner user."""
        assert self._session is not None
        body = {
            "client_id": CLIENT_ID,
            "name": ONBOARD_NAME,
            "username": ONBOARD_USER,
            "password": ONBOARD_PASS,
            "language": "en",
        }
        async with self._session.post(self._url("/api/onboarding/users"), json=body) as resp:
            data = await resp.json()
            if "auth_code" not in data:
                msg = f"Onboarding failed: {data}"
                raise RuntimeError(msg)
            return data["auth_code"]

    async def _exchange_auth_code(self, auth_code: str) -> str:
        """Exchange auth code for an access token."""
        assert self._session is not None
        form_data = aiohttp.FormData()
        form_data.add_field("grant_type", "authorization_code")
        form_data.add_field("code", auth_code)
        form_data.add_field("client_id", CLIENT_ID)

        async with self._session.post(
            self._url("/auth/token"),
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            data = await resp.json()
            if "access_token" not in data:
                msg = f"Token exchange failed: {data}"
                raise RuntimeError(msg)
            return data["access_token"]

    async def _create_long_lived_token(self, access_token: str) -> str:
        """Create a long-lived access token via WebSocket."""
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/api/websocket"

        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(connector=connector) as ws_session, ws_session.ws_connect(ws_url) as ws:
            # Read auth_required
            msg = await ws.receive_json()
            assert msg.get("type") == "auth_required"

            # Send auth
            await ws.send_json({"type": "auth", "access_token": access_token})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                err_msg = f"Auth failed: {msg}"
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

    # --- API methods ---

    async def get_health(self) -> HactlResult:
        """Get HA API status."""
        assert self._session is not None
        async with self._session.get(self._url("/api/")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)

    async def get_config(self) -> HactlResult:
        """Get HA config."""
        assert self._session is not None
        async with self._session.get(self._url("/api/config")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)

    async def get_services(self) -> HactlResult:
        """Get all registered services."""
        assert self._session is not None
        async with self._session.get(self._url("/api/services")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)

    async def get_states(self) -> HactlResult:
        """Get all entity states."""
        assert self._session is not None
        async with self._session.get(self._url("/api/states")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data={"states": data})

    async def get_config_entries(self) -> HactlResult:
        """Get config entries (integrations)."""
        assert self._session is not None
        async with self._session.get(self._url("/api/config/config_entries/entry")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data={"entries": data})

    async def create_config_entry(self, domain: str, data: dict[str, Any]) -> HactlResult:
        """Create a config entry via the config flow API."""
        assert self._session is not None

        # Start config flow
        async with self._session.post(
            self._url("/api/config/config_entries/flow"),
            json={"handler": domain, "show_advanced_options": False},
        ) as resp:
            flow = await resp.json()
            flow_id = flow.get("flow_id")
            if not flow_id:
                return HactlResult(status=resp.status, data=flow)

        # Submit form data
        async with self._session.post(
            self._url(f"/api/config/config_entries/flow/{flow_id}"),
            json=data,
        ) as resp:
            result = await resp.json()
            return HactlResult(status=resp.status, data=result)

    async def get_diagnostics(self, entry_id: str) -> HactlResult:
        """Get diagnostics for a config entry."""
        assert self._session is not None
        async with self._session.get(self._url(f"/api/diagnostics/config_entry/{entry_id}")) as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)

    async def reload_integration(self, entry_id: str) -> HactlResult:
        """Reload a config entry."""
        assert self._session is not None
        async with self._session.post(
            self._url("/api/config/config_entries/entry/reload"),
            json={"entry_id": entry_id},
        ) as resp:
            if resp.content_type == "application/json":
                data = await resp.json()
            else:
                data = {"status": "ok"}
            return HactlResult(status=resp.status, data=data)
