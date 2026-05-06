"""Repairs support for HEMM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.repairs import RepairsFlow

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

ISSUE_SOLVER_DEGRADED = "solver_degraded"


class HemmSolverDegradedRepairFlow(RepairsFlow):
    """Handler for solver degraded repair flow."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Handle the first step of the repair flow."""
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Handle confirmation."""
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: object,
    issue_id: str,
    data: dict | None,
) -> RepairsFlow:
    """Create repair flow."""
    return HemmSolverDegradedRepairFlow()
