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
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_ACTUATION_ENABLED,
    CONF_FEED_IN_TARIFF,
    CONF_GRID_EXPORT_LIMIT_KW,
    CONF_GRID_IMPORT_LIMIT_KW,
    CONF_HORIZON_HOURS,
    CONF_MAX_ITERATIONS,
    CONF_NAME,
    CONF_PRICE_ADAPTER,
    CONF_PRICE_ENTITY,
    CONF_SOLVER_BACKEND,
    CONF_WATCHDOG_TIMEOUT_SECONDS,
    CONF_WEATHER_ENTITY,
    DEFAULT_ACTUATION_ENABLED,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_NAME,
    DEFAULT_PRICE_ADAPTER,
    DEFAULT_SOLVER_BACKEND,
    DEFAULT_WATCHDOG_TIMEOUT_SECONDS,
    DOMAIN,
    PRICE_ADAPTERS,
    SOLVER_BACKENDS,
)
from .device_flow import HemmDeviceFlowMixin

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HORIZON_HOURS, default=DEFAULT_HORIZON_HOURS): vol.All(int, vol.Range(min=1, max=72)),
        vol.Required(CONF_MAX_ITERATIONS, default=DEFAULT_MAX_ITERATIONS): vol.All(int, vol.Range(min=5, max=500)),
        vol.Required(CONF_PRICE_ADAPTER, default=DEFAULT_PRICE_ADAPTER): vol.In(PRICE_ADAPTERS),
        # Real tariff source (FR-101): read live and passed to the adapter as a
        # pre-fetched series. Optional so the flow degrades to the adapter's own
        # fetch, but without it the template default refuses to run on flat data.
        vol.Optional(CONF_PRICE_ENTITY): EntitySelector(EntitySelectorConfig(domain="sensor")),
        vol.Required(CONF_SOLVER_BACKEND, default=DEFAULT_SOLVER_BACKEND): vol.In(SOLVER_BACKENDS),
    }
)


def _suggest(value: object) -> dict[str, object]:
    """Pre-fill an optional field with the current value without forcing a default."""
    return {"suggested_value": value} if value not in (None, "") else {}


class HemmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HEMM."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step — hub setup."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data={**user_input, "devices": []})

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HemmOptionsFlow:
        """Get the options flow for this handler."""
        return HemmOptionsFlow()


class HemmOptionsFlow(HemmDeviceFlowMixin, OptionsFlow):
    """Handle HEMM options — includes device management."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options — choose between settings and device management."""
        if user_input is not None:
            action = user_input.get("action", "settings")
            if action == "add_device":
                return await self.async_step_select_device()
            if action == "remove_device":
                return await self.async_step_remove_device()
            return await self.async_step_settings()

        schema = vol.Schema(
            {
                vol.Required("action", default="settings"): vol.In(
                    {
                        "settings": "Adjust settings",
                        "add_device": "Add a device",
                        "remove_device": "Remove a device",
                    }
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage hub settings."""
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
                vol.Optional(
                    CONF_PRICE_ENTITY,
                    description=_suggest(
                        self.config_entry.options.get(CONF_PRICE_ENTITY, self.config_entry.data.get(CONF_PRICE_ENTITY))
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_FEED_IN_TARIFF,
                    description=_suggest(
                        self.config_entry.options.get(
                            CONF_FEED_IN_TARIFF, self.config_entry.data.get(CONF_FEED_IN_TARIFF)
                        )
                    ),
                ): NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.001, mode=NumberSelectorMode.BOX)),
                # FR-201: grid/main-fuse connection limits. Empty = unbounded.
                vol.Optional(
                    CONF_GRID_IMPORT_LIMIT_KW,
                    description=_suggest(
                        self.config_entry.options.get(
                            CONF_GRID_IMPORT_LIMIT_KW, self.config_entry.data.get(CONF_GRID_IMPORT_LIMIT_KW)
                        )
                    ),
                ): NumberSelector(NumberSelectorConfig(min=1, max=200, step=0.1, mode=NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_GRID_EXPORT_LIMIT_KW,
                    description=_suggest(
                        self.config_entry.options.get(
                            CONF_GRID_EXPORT_LIMIT_KW, self.config_entry.data.get(CONF_GRID_EXPORT_LIMIT_KW)
                        )
                    ),
                ): NumberSelector(NumberSelectorConfig(min=1, max=200, step=0.1, mode=NumberSelectorMode.BOX)),
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    description=_suggest(
                        self.config_entry.options.get(
                            CONF_WEATHER_ENTITY, self.config_entry.data.get(CONF_WEATHER_ENTITY)
                        )
                    ),
                ): EntitySelector(EntitySelectorConfig(domain="weather")),
                vol.Required(
                    CONF_SOLVER_BACKEND,
                    default=self.config_entry.options.get(
                        CONF_SOLVER_BACKEND,
                        self.config_entry.data.get(CONF_SOLVER_BACKEND, DEFAULT_SOLVER_BACKEND),
                    ),
                ): vol.In(SOLVER_BACKENDS),
                vol.Optional(
                    CONF_ACTUATION_ENABLED,
                    default=self.config_entry.options.get(
                        CONF_ACTUATION_ENABLED,
                        self.config_entry.data.get(CONF_ACTUATION_ENABLED, DEFAULT_ACTUATION_ENABLED),
                    ),
                ): bool,
                vol.Optional(
                    CONF_WATCHDOG_TIMEOUT_SECONDS,
                    default=self.config_entry.options.get(
                        CONF_WATCHDOG_TIMEOUT_SECONDS,
                        self.config_entry.data.get(CONF_WATCHDOG_TIMEOUT_SECONDS, DEFAULT_WATCHDOG_TIMEOUT_SECONDS),
                    ),
                ): vol.All(int, vol.Range(min=60, max=86400)),
            }
        )

        return self.async_show_form(step_id="settings", data_schema=options_schema)
