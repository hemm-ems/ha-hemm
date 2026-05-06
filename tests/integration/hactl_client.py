"""Hactl client — minimal REST client for interacting with HA container."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass
class HactlResult:
    """Result from an hactl API call."""

    status: int
    data: dict[str, Any]


class HactlClient:
    """Minimal client for interacting with HA container via REST API."""

    def __init__(self, base_url: str = "http://localhost:8123", token: str = "") -> None:
        self._base_url = base_url
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> HactlClient:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._session = aiohttp.ClientSession(headers=headers)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._session:
            await self._session.close()

    async def check_health(self, timeout: float = 30.0) -> bool:
        """Wait for HA to become healthy."""
        assert self._session is not None
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with self._session.get(f"{self._base_url}/api/") as resp:
                    if resp.status == 200:
                        return True
            except (aiohttp.ClientError, OSError):
                pass
            await asyncio.sleep(1.0)
        return False

    async def get_config(self) -> HactlResult:
        """Get HA config."""
        assert self._session is not None
        async with self._session.get(f"{self._base_url}/api/config") as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)

    async def get_services(self) -> HactlResult:
        """Get all registered services."""
        assert self._session is not None
        async with self._session.get(f"{self._base_url}/api/services") as resp:
            data = await resp.json()
            return HactlResult(status=resp.status, data=data)
