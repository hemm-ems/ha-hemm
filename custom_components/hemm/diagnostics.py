"""Diagnostics support for HEMM."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, TESTED_HA_VERSION
from .coordinator import HemmCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HemmCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    # Active constraint windows
    try:
        active_windows = coordinator.constraint_manager.get_active()
        windows_data = [
            {
                "window_id": w.window_id,
                "device_id": w.device_id,
                "deadline": w.deadline.isoformat(),
                "priority_penalty": w.priority_penalty,
            }
            for w in active_windows
        ]
    except (ImportError, ModuleNotFoundError):
        windows_data = []

    # Last solver result info
    last_result = coordinator.last_result
    solver_diagnostics: dict[str, Any] = {}
    if last_result:
        solver_diagnostics = {
            "status": last_result.status.value,
            "objective_value": last_result.objective_value,
            "solve_time_seconds": last_result.solve_time_seconds,
            "iterations": last_result.iterations,
            "plan_count": len(last_result.plans),
            "solver_diagnostics": {str(k): str(v) for k, v in last_result.diagnostics.items()}
            if last_result.diagnostics
            else {},
        }

    return {
        "tested_ha_version": TESTED_HA_VERSION,
        "config_entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator_state": {
            "horizon_hours": coordinator.horizon_hours,
            "solver_backend": coordinator.solver_backend,
            "price_adapter": coordinator.price_adapter,
            "last_plans": data.get("last_plans", []),
            "iteration_count": data.get("iteration_count", 0),
            "last_status": data.get("last_status"),
            "last_solve_time": data.get("last_solve_time"),
            "actuation_enabled": coordinator.actuation_enabled,
            "watchdog_timeout_seconds": coordinator.watchdog_timeout_seconds,
        },
        "active_constraint_windows": windows_data,
        "last_solver_result": solver_diagnostics,
        "lambda_history": list(coordinator._lambda_history),
        "dry_run_log": coordinator.dry_run_log,
        "actuation_audit_log": coordinator.actuation_audit_log,
        "identification_results": coordinator.id_results,
    }
