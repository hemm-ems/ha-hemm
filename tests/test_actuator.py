"""Unit tests for Phase 7 verified actuation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.hemm.actuator import ActuationDecision, ActuatorEngine, evaluate_expected


class AdvancingClock:
    """Small test clock that advances on each read."""

    def __init__(self) -> None:
        self._now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        self._now += timedelta(seconds=0.02)
        return self._now


def _action(
    script: str,
    *,
    verify_entity: str | None = "sensor.verify_target",
    expected: str = "== on",
    max_attempts: int = 1,
    writes_entity: str | None = None,
) -> Any:
    from hemm_core.manifest.types import Action, RetryPolicy, VerificationContract

    action = Action(
        script=script,
        verify=VerificationContract(entity=verify_entity, expected=expected, within_seconds=0.01)
        if verify_entity
        else None,
        retry=RetryPolicy(max_attempts=max_attempts, backoff_seconds=0),
    )
    if writes_entity is not None:
        object.__setattr__(action, "writes_entity", writes_entity)
    return action


async def _register_script(hass: HomeAssistant, name: str, calls: list[str]) -> None:
    async def handler(call: ServiceCall) -> None:
        calls.append(f"script.{name}")

    hass.services.async_register("script", name, handler)


@pytest.mark.unit
@pytest.mark.req("010:FR-001")
def test_expected_expression_parser_numeric_and_string() -> None:
    assert evaluate_expected("61", ">= 60")
    assert evaluate_expected("60", "<= 60")
    assert evaluate_expected("off", "== OFF")
    assert evaluate_expected("heat", "!= cool")
    assert not evaluate_expected("cold", ">= 60")
    assert not evaluate_expected("on", "== off")


@pytest.mark.unit
@pytest.mark.req("010:FR-002", "010:FR-007")
async def test_read_only_default_skips_call_and_anonymizes_audit(hass: HomeAssistant) -> None:
    calls: list[str] = []
    await _register_script(hass, "secret_boiler_heat", calls)
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    decision = ActuationDecision(
        device_id="jane_kitchen_boiler",
        action=_action("script.secret_boiler_heat", verify_entity="sensor.jane_kitchen_temperature"),
        safe_default=_action("script.secret_boiler_heat", verify_entity=None),
    )

    outcome = await engine.async_actuate(decision, actuation_enabled=False)

    assert outcome == "skipped:read_only"
    assert calls == []
    audit_blob = json.dumps(engine.audit_log)
    assert "skipped:read_only" in audit_blob
    assert "jane_kitchen_boiler" not in audit_blob
    assert "secret_boiler_heat" not in audit_blob
    assert "sensor.jane_kitchen_temperature" not in audit_blob


@pytest.mark.unit
@pytest.mark.req("010:FR-003")
async def test_dry_run_evaluates_verification_without_calling_script(hass: HomeAssistant) -> None:
    calls: list[str] = []
    await _register_script(hass, "turn_on", calls)
    hass.states.async_set("sensor.verify_target", "on")
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    decision = ActuationDecision(
        device_id="battery",
        action=_action("script.turn_on"),
        safe_default=_action("script.turn_on", verify_entity=None),
    )

    outcome = await engine.async_actuate(decision, actuation_enabled=True, dry_run=True)

    assert outcome == "dry_run"
    assert calls == []
    assert engine.audit_log[-1]["outcome"] == "dry_run"
    assert engine.audit_log[-1]["verified"] is True


@pytest.mark.unit
@pytest.mark.req("010:FR-001")
async def test_verify_failure_retries_then_safe_default_and_repair(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    repairs: list[str] = []
    await _register_script(hass, "turn_on", calls)
    await _register_script(hass, "safe_off", calls)
    hass.states.async_set("sensor.verify_target", "off")

    async def fake_repair(_hass: HomeAssistant, *, device_id: str) -> None:
        repairs.append(device_id)

    monkeypatch.setattr("custom_components.hemm.actuator.async_create_verify_failed_issue", fake_repair)
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    decision = ActuationDecision(
        device_id="ev",
        action=_action("script.turn_on", max_attempts=2),
        safe_default=_action("script.safe_off", verify_entity=None),
    )

    outcome = await engine.async_actuate(decision, actuation_enabled=True)

    assert outcome == "safe_default"
    assert calls == ["script.turn_on", "script.turn_on", "script.safe_off"]
    assert repairs == ["ev"]
    assert [entry["outcome"] for entry in engine.audit_log] == ["retried", "safe_default"]


@pytest.mark.unit
@pytest.mark.req("010:FR-004")
async def test_pre_call_check_failure_skips_action_and_invokes_safe_default(hass: HomeAssistant) -> None:
    calls: list[str] = []
    await _register_script(hass, "turn_on", calls)
    await _register_script(hass, "safe_off", calls)
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    decision = ActuationDecision(
        device_id="hp",
        action=_action("script.turn_on"),
        safe_default=_action("script.safe_off", verify_entity=None),
    )

    async def deny(_device_id: str, _action_obj: Any) -> bool:
        return False

    outcome = await engine.async_actuate(decision, actuation_enabled=True, pre_call_check=deny)

    assert outcome == "safe_default"
    assert calls == ["script.safe_off"]
    assert engine.audit_log[-1]["reason"] == "pre_call_failed"


@pytest.mark.unit
@pytest.mark.req("010:FR-006")
async def test_override_skips_only_selected_device(hass: HomeAssistant) -> None:
    calls: list[str] = []
    await _register_script(hass, "a", calls)
    await _register_script(hass, "b", calls)
    hass.states.async_set("sensor.verify_target", "on")
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    engine.set_override("a", True)

    skipped = await engine.async_actuate(
        ActuationDecision("a", _action("script.a"), _action("script.a", verify_entity=None)),
        actuation_enabled=True,
    )
    verified = await engine.async_actuate(
        ActuationDecision("b", _action("script.b"), _action("script.b", verify_entity=None)),
        actuation_enabled=True,
    )

    assert skipped == "skipped:override"
    assert verified == "verified"
    assert calls == ["script.b"]


@pytest.mark.unit
@pytest.mark.req("010:FR-005")
async def test_watchdog_safe_default_ignores_read_only_and_override(hass: HomeAssistant) -> None:
    calls: list[str] = []
    await _register_script(hass, "safe_a", calls)
    await _register_script(hass, "safe_b", calls)
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    engine.set_override("a", True)

    await engine.async_watchdog_safe_defaults(
        [
            ActuationDecision(
                "a", _action("script.safe_a", verify_entity=None), _action("script.safe_a", verify_entity=None)
            ),
            ActuationDecision(
                "b", _action("script.safe_b", verify_entity=None), _action("script.safe_b", verify_entity=None)
            ),
        ],
        reason="watchdog_timeout",
    )

    assert calls == ["script.safe_a", "script.safe_b"]
    assert [entry["outcome"] for entry in engine.audit_log] == ["safe_default", "safe_default"]


@pytest.mark.unit
@pytest.mark.req("010:FR-001")
async def test_self_confirming_contract_refuses_action_and_raises_repair(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    repairs: list[str] = []
    await _register_script(hass, "turn_on", calls)

    async def fake_repair(_hass: HomeAssistant, *, device_id: str) -> None:
        repairs.append(device_id)

    monkeypatch.setattr("custom_components.hemm.actuator.async_create_self_confirming_issue", fake_repair)
    engine = ActuatorEngine(hass, clock=AdvancingClock())
    decision = ActuationDecision(
        device_id="heater",
        action=_action("script.turn_on", verify_entity="switch.heater", writes_entity="switch.heater"),
        safe_default=_action("script.turn_on", verify_entity=None),
    )

    outcome = await engine.async_actuate(decision, actuation_enabled=True)

    assert outcome == "skipped:self_confirming"
    assert calls == []
    assert repairs == ["heater"]
