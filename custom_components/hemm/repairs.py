"""Repairs support for HEMM."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)

ISSUE_SOLVER_DEGRADED = "solver_degraded"
ISSUE_VERIFY_FAILED = "actuation_verify_failed"
ISSUE_SELF_CONFIRMING = "actuation_self_confirming"
ISSUE_PRICE_UNAVAILABLE = "price_unavailable"


class _ConfirmOnlyRepairFlow(RepairsFlow):
    """Base for repair flows that just show a confirm step then close.

    HEMM has no automated remediation for any of these issues — the user
    reviews their config/script and dismisses the issue. Subclasses exist
    so callers (and tests) can dispatch/assert on a semantically distinct
    flow type per issue, even though the steps are identical.
    """

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Handle the first step of the repair flow."""
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Handle confirmation."""
        return self.async_create_entry(data={})


class HemmSolverDegradedRepairFlow(_ConfirmOnlyRepairFlow):
    """Handler for solver degraded repair flow."""


class HemmPriceUnavailableRepairFlow(_ConfirmOnlyRepairFlow):
    """Handler for the price-source-unavailable repair flow.

    Raised (FR-102) when the configured price source can't be read: HEMM
    refuses to optimize on synthetic data and skips the solve until the
    tariff source recovers. The fix is the user pointing HEMM at a working
    price entity / adapter, then confirming.
    """


class HemmActuationRepairFlow(_ConfirmOnlyRepairFlow):
    """Handler for actuation verification repair flows.

    Covers both actuation_verify_failed and actuation_self_confirming: HEMM
    already invoked the device's safe default before raising either issue,
    so the fix is the user reviewing/adjusting the script or verification
    entity, then confirming.
    """


class ConfirmRepairFlow(_ConfirmOnlyRepairFlow):
    """Generic confirm-only fallback for an issue_id we don't recognize."""


async def async_create_verify_failed_issue(hass: HomeAssistant, *, device_id: str) -> None:
    """Raise a repair issue for terminal verification failure."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_VERIFY_FAILED}_{device_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_VERIFY_FAILED,
        translation_placeholders={"device_id": device_id},
    )


async def async_create_self_confirming_issue(hass: HomeAssistant, *, device_id: str) -> None:
    """Raise a repair issue for a self-confirming verification contract."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_SELF_CONFIRMING}_{device_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_SELF_CONFIRMING,
        translation_placeholders={"device_id": device_id},
    )


async def async_create_price_unavailable_issue(hass: HomeAssistant) -> None:
    """Raise a repair issue when the configured price source can't be read (FR-102)."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_PRICE_UNAVAILABLE,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PRICE_UNAVAILABLE,
    )


def async_clear_price_unavailable_issue(hass: HomeAssistant) -> None:
    """Clear the price-unavailable repair issue once a real price series is read again."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_PRICE_UNAVAILABLE)


async def async_create_fix_flow(
    hass: object,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create repair flow.

    Dispatches on issue_id rather than always returning the solver-degraded
    flow. actuation_verify_failed and actuation_self_confirming issues carry
    a device_id fingerprint suffix (see async_create_verify_failed_issue and
    async_create_self_confirming_issue above), so match by prefix.
    """
    if issue_id == ISSUE_SOLVER_DEGRADED:
        return HemmSolverDegradedRepairFlow()
    if issue_id == ISSUE_PRICE_UNAVAILABLE:
        return HemmPriceUnavailableRepairFlow()
    if issue_id.startswith(f"{ISSUE_VERIFY_FAILED}_") or issue_id.startswith(f"{ISSUE_SELF_CONFIRMING}_"):
        return HemmActuationRepairFlow()
    _LOGGER.warning("async_create_fix_flow: no dispatch known for issue_id=%s, using generic confirm flow", issue_id)
    return ConfirmRepairFlow()
