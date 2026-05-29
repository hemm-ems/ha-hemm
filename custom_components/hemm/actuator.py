"""Verified actuation engine for HEMM."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import operator
import re
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .repairs import async_create_self_confirming_issue, async_create_verify_failed_issue

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from hemm_core.time import Clock

_LOGGER = logging.getLogger(__name__)

MAX_AUDIT_ENTRIES = 100
VERIFY_POLL_SECONDS = 0.25

_EXPECTED_RE = re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*(.*?)\s*$")
_NUMERIC_OPS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}
_STRING_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
}


class PreCallCheck(Protocol):
    """Async pre-call safety check hook."""

    def __call__(self, device_id: str, action: Any) -> Awaitable[bool]:
        """Return whether the action may be called."""


@dataclass(frozen=True)
class ActuationDecision:
    """A planned device action selected by the coordinator."""

    device_id: str
    action: Any
    safe_default: Any
    plan_mode: str | None = None


def evaluate_expected(actual: Any, expected: str) -> bool:
    """Evaluate a VerificationContract expected expression against a HA state value."""
    state = getattr(actual, "state", actual)
    actual_text = "" if state is None else str(state).strip()

    match = _EXPECTED_RE.match(expected)
    if match is None:
        msg = f"Unsupported verification expression: {expected!r}"
        raise ValueError(msg)

    op, rhs = match.groups()
    rhs_text = rhs.strip()

    if op in _NUMERIC_OPS:
        try:
            return bool(_NUMERIC_OPS[op](float(actual_text), float(rhs_text)))
        except ValueError:
            return False

    try:
        return bool(_STRING_OPS[op](float(actual_text), float(rhs_text)))
    except ValueError:
        return bool(_STRING_OPS[op](actual_text.casefold(), rhs_text.casefold()))


def _fingerprint(value: Any) -> str:
    """Return a stable short hash for PII-bearing audit fields."""
    raw = "" if value is None else str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _script_service(action: Any) -> tuple[str, str]:
    """Split an Action.script into HA domain/service parts."""
    script = str(getattr(action, "script", ""))
    domain, sep, service = script.partition(".")
    if not sep or not domain or not service:
        msg = f"Invalid HA script entity: {script!r}"
        raise ValueError(msg)
    return domain, service


def _action_retry(action: Any) -> tuple[int, float]:
    """Return bounded retry settings from an Action."""
    retry = getattr(action, "retry", None)
    attempts = int(getattr(retry, "max_attempts", 1) or 1)
    backoff = float(getattr(retry, "backoff_seconds", 0) or 0)
    return max(1, attempts), max(0.0, backoff)


def _writes_entity(action: Any) -> str | None:
    """Read optional non-core writes_entity metadata from dicts or test doubles."""
    value = action.get("writes_entity") if isinstance(action, dict) else getattr(action, "writes_entity", None)
    return str(value) if value else None


class ActuatorEngine:
    """Call user scripts, verify outcomes, and drive safe defaults."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        clock: Clock,
        audit_maxlen: int = MAX_AUDIT_ENTRIES,
    ) -> None:
        self.hass = hass
        self._clock = clock
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=audit_maxlen)
        self._overrides: dict[str, bool] = {}

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Return anonymized actuation audit entries."""
        return list(self._audit_log)

    def is_override_enabled(self, device_id: str) -> bool:
        """Return whether a device override is active."""
        return self._overrides.get(device_id, False)

    def set_override(self, device_id: str, enabled: bool) -> None:
        """Set the in-memory override state for a device."""
        self._overrides[device_id] = enabled

    async def async_actuate(
        self,
        decision: ActuationDecision,
        *,
        actuation_enabled: bool,
        dry_run: bool = False,
        pre_call_check: PreCallCheck | None = None,
    ) -> str:
        """Actuate one decided action and return its terminal outcome."""
        device_id = decision.device_id

        if dry_run:
            verified = await self._async_verify(decision.action)
            self._record(decision, "dry_run", verified=verified)
            return "dry_run"

        if not actuation_enabled:
            self._record(decision, "skipped:read_only")
            return "skipped:read_only"

        if self.is_override_enabled(device_id):
            self._record(decision, "skipped:override")
            return "skipped:override"

        if self._is_self_confirming(decision.action):
            self._record(decision, "skipped:self_confirming")
            await async_create_self_confirming_issue(self.hass, device_id=device_id)
            return "skipped:self_confirming"

        attempts, backoff = _action_retry(decision.action)
        for attempt in range(1, attempts + 1):
            if pre_call_check is not None and not await pre_call_check(device_id, decision.action):
                await self.async_invoke_safe_default(decision, reason="pre_call_failed")
                return "safe_default"

            await self._async_call_script(decision.action)

            if getattr(decision.action, "verify", None) is None:
                self._record(decision, "unverified", attempt=attempt)
                return "unverified"

            if await self._async_wait_verified(decision.action):
                self._record(decision, "verified", attempt=attempt)
                return "verified"

            if attempt < attempts:
                self._record(decision, "retried", attempt=attempt)
                if backoff:
                    await asyncio.sleep(backoff)

        await self.async_invoke_safe_default(decision, reason="verify_failed")
        await async_create_verify_failed_issue(self.hass, device_id=device_id)
        return "safe_default"

    async def async_invoke_safe_default(self, decision: ActuationDecision, *, reason: str) -> None:
        """Invoke a device safe default regardless of read-only or override state."""
        await self._async_call_script(decision.safe_default)
        verified = await self._async_verify(decision.safe_default)
        self._record(decision, "safe_default", reason=reason, verified=verified, action=decision.safe_default)

    async def async_watchdog_safe_defaults(self, decisions: list[ActuationDecision], *, reason: str) -> None:
        """Invoke safe defaults for every device selected by the watchdog."""
        for decision in decisions:
            await self.async_invoke_safe_default(decision, reason=reason)

    async def _async_call_script(self, action: Any) -> None:
        """Call the user-owned HA script referenced by the action."""
        domain, service = _script_service(action)
        await self.hass.services.async_call(domain, service, {}, blocking=True)

    async def _async_wait_verified(self, action: Any) -> bool:
        """Poll a verify entity until it matches or the contract times out."""
        verify = getattr(action, "verify", None)
        if verify is None:
            return False

        deadline = self._clock.now() + timedelta(seconds=float(verify.within_seconds))
        while True:
            if await self._async_verify(action):
                return True
            if self._clock.now() >= deadline:
                return False
            await asyncio.sleep(min(VERIFY_POLL_SECONDS, max(0.01, float(verify.within_seconds) / 10)))

    async def _async_verify(self, action: Any) -> bool:
        """Evaluate an action's verification contract once."""
        verify = getattr(action, "verify", None)
        if verify is None:
            return False
        state = self.hass.states.get(verify.entity)
        if state is None:
            return False
        return evaluate_expected(state.state, verify.expected)

    def _is_self_confirming(self, action: Any) -> bool:
        """Return true when explicit writes_entity metadata matches verify.entity."""
        verify = getattr(action, "verify", None)
        writes_entity = _writes_entity(action)
        return bool(verify is not None and writes_entity and writes_entity == verify.entity)

    def _record(
        self,
        decision: ActuationDecision,
        outcome: str,
        *,
        attempt: int | None = None,
        verified: bool | None = None,
        reason: str | None = None,
        action: Any | None = None,
    ) -> None:
        """Append a PII-safe audit entry."""
        audited_action = action or decision.action
        verify = getattr(audited_action, "verify", None)
        entry: dict[str, Any] = {
            "timestamp": self._clock.now().isoformat(),
            "outcome": outcome,
            "device_hash": _fingerprint(decision.device_id),
            "script_hash": _fingerprint(getattr(audited_action, "script", "")),
        }
        if decision.plan_mode:
            entry["plan_mode_hash"] = _fingerprint(decision.plan_mode)
        if verify is not None:
            entry["verify_entity_hash"] = _fingerprint(verify.entity)
            entry["expected_hash"] = _fingerprint(verify.expected)
        if attempt is not None:
            entry["attempt"] = attempt
        if verified is not None:
            entry["verified"] = verified
        if reason is not None:
            entry["reason"] = reason
        self._audit_log.append(entry)
        self.hass.bus.async_fire(f"{DOMAIN}_actuation_audit", entry)
        _LOGGER.debug("HEMM actuation audit entry: %s", entry)
