"""Config flow for HEMM integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_SOLVER_BACKEND,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_NAME,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DOMAIN,
    PRICE_ADAPTERS,
    SOLVER_BACKENDS,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HORIZON_HOURS, default=DEFAULT_HORIZON_HOURS): vol.All(int, vol.Range(min=1, max=72)),
        vol.Required(CONF_MAX_ITERATIONS, default=DEFAULT_MAX_ITERATIONS): vol.All(int, vol.Range(min=5, max=500)),
        vol.Required(CONF_PRICE_ADAPTER, default=DEFAULT_PRICE_ADAPTER): vol.In(PRICE_ADAPTERS),
        vol.Required(CONF_SOLVER_BACKEND, default=DEFAULT_SOLVER_BACKEND): vol.In(SOLVER_BACKENDS),
    }
)


class HemmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HEMM."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step — hub setup."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HemmOptionsFlow:
        """Get the options flow for this handler."""
        return HemmOptionsFlow()


class HemmOptionsFlow(OptionsFlow):
    """Handle HEMM options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HORIZON_HOURS,
                    default=self.config_entry.options.get(
                        CONF_HORIZON_HOURS,
                        self.config_entry.data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
                    ),
                ): vol.All(int, vol.Range(min=1, max=72)),
                vol.Required(
                    CONF_MAX_ITERATIONS,
                    default=self.config_entry.options.get(
                        CONF_MAX_ITERATIONS,
                        self.config_entry.data.get(CONF_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS),
                    ),
                ): vol.All(int, vol.Range(min=5, max=500)),
                vol.Required(
                    CONF_PRICE_ADAPTER,
                    default=self.config_entry.options.get(
                        CONF_PRICE_ADAPTER,
                        self.config_entry.data.get(CONF_PRICE_ADAPTER, DEFAULT_PRICE_ADAPTER),
                    ),
                ): vol.In(PRICE_ADAPTERS),
                vol.Required(
                    CONF_SOLVER_BACKEND,
                    default=self.config_entry.options.get(
                        CONF_SOLVER_BACKEND,
                        self.config_entry.data.get(CONF_SOLVER_BACKEND, DEFAULT_SOLVER_BACKEND),
                    ),
                ): vol.In(SOLVER_BACKENDS),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
