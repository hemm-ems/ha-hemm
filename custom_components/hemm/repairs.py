"""Repairs support for HEMM."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

ISSUE_SOLVER_DEGRADED = "solver_degraded"
ISSUE_VERIFY_FAILED = "actuation_verify_failed"
ISSUE_SELF_CONFIRMING = "actuation_self_confirming"


class HemmSolverDegradedRepairFlow(RepairsFlow):
    """Handler for solver degraded repair flow."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Handle the first step of the repair flow."""
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Handle confirmation."""
        return self.async_create_entry(data={})


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


async def async_create_fix_flow(
    hass: object,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create repair flow."""
    return HemmSolverDegradedRepairFlow()
