"""HEMM services — plug point 1 for automations and developer tools."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DRY_RUN,
    DOMAIN,
    SERVICE_ADD_CONSTRAINT,
    SERVICE_BUMP_PRIORITY,
    SERVICE_REMOVE_CONSTRAINT,
    SERVICE_REPLAN,
    SERVICE_SET_PRICE_CURVE,
    SERVICE_SET_SOLVER,
    SERVICE_SIMULATE,
    SERVICE_TICK,
    SOLVER_BACKENDS,
)
from .coordinator import HemmCoordinator

_LOGGER = logging.getLogger(__name__)


# Constraint type to model mapping
_REQUIREMENT_BUILDERS: dict[str, type] = {}


def _init_requirement_builders() -> None:
    """Lazily init the requirement builders."""
    if _REQUIREMENT_BUILDERS:
        return
    from hemm.manifest.constraints import (
        ForbiddenWindow,
        HoldTempBand,
        MaxRuntimePerDay,
        MinEnergyUntil,
        MinRuntimePerDay,
        MinSocUntil,
        ReachMinTempOnce,
    )

    _REQUIREMENT_BUILDERS.update(
        {
            "reach_min_temp_once": ReachMinTempOnce,
            "hold_temp_band": HoldTempBand,
            "min_soc_until": MinSocUntil,
            "min_energy_until": MinEnergyUntil,
            "forbidden_window": ForbiddenWindow,
            "min_runtime_per_day": MinRuntimePerDay,
            "max_runtime_per_day": MaxRuntimePerDay,
        }
    )


def _build_requirement(req_type: str, req_params: dict[str, Any]) -> Any:
    """Build a constraint requirement from type name and parameters."""
    _init_requirement_builders()
    cls = _REQUIREMENT_BUILDERS.get(req_type)
    if cls is None:
        msg = f"Unknown constraint type: {req_type}"
        raise vol.Invalid(msg)
    return cls(**req_params)


def _get_coordinator(hass: HomeAssistant) -> HemmCoordinator:
    """Get the first HEMM coordinator (single-instance integration)."""
    if DOMAIN not in hass.data:
        msg = "HEMM integration not loaded"
        raise ValueError(msg)
    entries = hass.data[DOMAIN]
    if not entries:
        msg = "No HEMM config entries found"
        raise ValueError(msg)
    return next(iter(entries.values()))


REPLAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
        vol.Optional("device_filter"): vol.All(cv.ensure_list, [cv.string]),
    }
)

SIMULATE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
        vol.Optional("horizon_hours"): vol.Coerce(int),
    }
)

SET_PRICE_CURVE_SCHEMA = vol.Schema(
    {
        vol.Required("prices"): vol.All(cv.ensure_list, [vol.Coerce(float)]),
        vol.Optional("resolution_minutes", default=15): vol.Coerce(int),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

SET_SOLVER_SCHEMA = vol.Schema(
    {
        vol.Required("backend"): vol.In(SOLVER_BACKENDS),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

ADD_CONSTRAINT_SCHEMA = vol.Schema(
    {
        vol.Required("window_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Required("deadline"): cv.datetime,
        vol.Required("requirement_type"): cv.string,
        vol.Optional("requirement_params", default={}): dict,
        vol.Optional("flex_cost_per_hour_early", default=0.0): vol.Coerce(float),
        vol.Optional("priority_penalty", default=1.0): vol.Coerce(float),
        vol.Optional("ttl_seconds"): vol.Coerce(float),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

REMOVE_CONSTRAINT_SCHEMA = vol.Schema(
    {
        vol.Required("window_id"): cv.string,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

BUMP_PRIORITY_SCHEMA = vol.Schema(
    {
        vol.Required("window_id"): cv.string,
        vol.Required("new_penalty"): vol.Coerce(float),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

TICK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register all HEMM services."""

    async def handle_replan(call: ServiceCall) -> None:
        """Handle hemm.replan service call."""
        coordinator = _get_coordinator(hass)
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        device_filter = call.data.get("device_filter")
        _LOGGER.info("hemm.replan called (dry_run=%s, device_filter=%s)", dry_run, device_filter)
        result = await coordinator.async_run_solver(dry_run=dry_run, device_filter=device_filter)
        if not dry_run:
            await coordinator.async_request_refresh()
        _LOGGER.info("Replan complete: status=%s, time=%.2fs", result.status, result.solve_time_seconds)

    async def handle_simulate(call: ServiceCall) -> None:
        """Handle hemm.simulate service call."""
        coordinator = _get_coordinator(hass)
        _LOGGER.info("hemm.simulate called")
        # Simulate is always a dry run
        await coordinator.async_run_solver(dry_run=True)

    async def handle_set_price_curve(call: ServiceCall) -> None:
        """Handle hemm.set_price_curve service call."""
        coordinator = _get_coordinator(hass)
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        _LOGGER.info("hemm.set_price_curve called (dry_run=%s)", dry_run)
        if not dry_run:
            # Store the manual price curve for next solver run
            # The coordinator will use it instead of fetching from adapter
            coordinator._manual_prices = call.data.get("prices", [])
            coordinator._manual_price_resolution = call.data.get("resolution_minutes", 15)

    async def handle_set_solver(call: ServiceCall) -> None:
        """Handle hemm.set_solver service call."""
        coordinator = _get_coordinator(hass)
        backend = call.data["backend"]
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        _LOGGER.info("hemm.set_solver called: backend=%s (dry_run=%s)", backend, dry_run)
        if not dry_run:
            coordinator.switch_solver(backend)

    async def handle_add_constraint(call: ServiceCall) -> None:
        """Handle hemm.add_constraint_window service call."""
        from hemm.manifest.messages import ConstraintWindow

        coordinator = _get_coordinator(hass)
        dry_run = call.data.get(ATTR_DRY_RUN, False)

        requirement = _build_requirement(
            call.data["requirement_type"],
            call.data.get("requirement_params", {}),
        )

        window = ConstraintWindow(
            window_id=call.data["window_id"],
            device_id=call.data["device_id"],
            deadline=call.data["deadline"],
            requirement=requirement,
            flex_cost_per_hour_early=call.data.get("flex_cost_per_hour_early", 0.0),
            priority_penalty=call.data.get("priority_penalty", 1.0),
            ttl_seconds=call.data.get("ttl_seconds"),
            created_at=dt_util.utcnow(),
        )

        _LOGGER.info(
            "hemm.add_constraint_window: id=%s device=%s (dry_run=%s)",
            window.window_id,
            window.device_id,
            dry_run,
        )
        if not dry_run:
            coordinator.add_constraint_window(window)

    async def handle_remove_constraint(call: ServiceCall) -> None:
        """Handle hemm.remove_constraint service call."""
        coordinator = _get_coordinator(hass)
        window_id = call.data["window_id"]
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        _LOGGER.info("hemm.remove_constraint: id=%s (dry_run=%s)", window_id, dry_run)
        if not dry_run:
            coordinator.remove_constraint(window_id)

    async def handle_bump_priority(call: ServiceCall) -> None:
        """Handle hemm.bump_priority service call."""
        coordinator = _get_coordinator(hass)
        window_id = call.data["window_id"]
        new_penalty = call.data["new_penalty"]
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        _LOGGER.info(
            "hemm.bump_priority: id=%s penalty=%s (dry_run=%s)",
            window_id,
            new_penalty,
            dry_run,
        )
        if not dry_run:
            coordinator.bump_priority(window_id, new_penalty)

    async def handle_tick(call: ServiceCall) -> None:
        """Handle hemm.tick service call — manual optimizer trigger."""
        coordinator = _get_coordinator(hass)
        dry_run = call.data.get(ATTR_DRY_RUN, False)
        _LOGGER.info("hemm.tick called (dry_run=%s)", dry_run)
        result = await coordinator.async_run_solver(dry_run=dry_run)
        if not dry_run:
            await coordinator.async_request_refresh()
        _LOGGER.info("Tick complete: status=%s", result.status)

    hass.services.async_register(DOMAIN, SERVICE_REPLAN, handle_replan, schema=REPLAN_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SIMULATE, handle_simulate, schema=SIMULATE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_PRICE_CURVE, handle_set_price_curve, schema=SET_PRICE_CURVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SOLVER, handle_set_solver, schema=SET_SOLVER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ADD_CONSTRAINT, handle_add_constraint, schema=ADD_CONSTRAINT_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_CONSTRAINT, handle_remove_constraint, schema=REMOVE_CONSTRAINT_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_BUMP_PRIORITY, handle_bump_priority, schema=BUMP_PRIORITY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_TICK, handle_tick, schema=TICK_SCHEMA)


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister HEMM services."""
    for service in (
        SERVICE_REPLAN,
        SERVICE_SIMULATE,
        SERVICE_SET_PRICE_CURVE,
        SERVICE_SET_SOLVER,
        SERVICE_ADD_CONSTRAINT,
        SERVICE_REMOVE_CONSTRAINT,
        SERVICE_BUMP_PRIORITY,
        SERVICE_TICK,
    ):
        hass.services.async_remove(DOMAIN, service)
