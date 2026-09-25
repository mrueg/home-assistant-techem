"""Config flow for the techem integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback

from . import api
from .const import (
    CONF_BILLING_START_MONTH,
    CONF_REFRESH_TOKEN,
    CONF_SKIP_IMPLAUSIBLE,
    CONF_UNIT_ID,
    DEFAULT_BILLING_START_MONTH,
    DOMAIN,
)
from .coordinator import async_login

_LOGGER = logging.getLogger(__package__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class NoRentalAgreement(api.TechemError):
    """Error to indicate the account has no residential unit."""


async def validate_input(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> dict[str, Any]:
    """Log in with the given credentials and return the data to store."""
    tokens = await async_login(hass, data[CONF_USERNAME], data[CONF_PASSWORD])
    if not tokens.unit_ids:
        raise NoRentalAgreement
    return {CONF_UNIT_ID: tokens.unit_ids[0], CONF_REFRESH_TOKEN: tokens.refresh_token}


def _errors_for(err: Exception) -> dict[str, str]:
    if isinstance(err, api.TechemAuthError):
        return {"base": "invalid_auth"}
    if isinstance(err, api.TechemInteractionRequired):
        return {"base": "interaction_required"}
    if isinstance(err, api.TechemConnectionError):
        return {"base": "cannot_connect"}
    if isinstance(err, NoRentalAgreement):
        return {"base": "no_rental_agreement"}
    _LOGGER.exception("Unexpected exception")
    return {"base": "unknown"}


class TechemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Techem."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TechemOptionsFlow:
        """Return the options flow."""
        return TechemOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, user_input)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data={**user_input, **info}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauthentication request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                info = await validate_input(self.hass, data)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={**user_input, **info}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the email address or password."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            unique_id = user_input[CONF_USERNAME].lower()
            if unique_id != entry.unique_id:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, user_input)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                # The sensors and statistics belong to the residential unit
                if info[CONF_UNIT_ID] != entry.data[CONF_UNIT_ID]:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=user_input[CONF_USERNAME],
                    data_updates={**user_input, **info},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                user_input or {CONF_USERNAME: entry.data[CONF_USERNAME]},
            ),
            errors=errors,
        )


class TechemOptionsFlow(OptionsFlowWithReload):
    """Handle the options of the integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_SKIP_IMPLAUSIBLE, default=False): bool,
                        vol.Required(
                            CONF_BILLING_START_MONTH,
                            default=DEFAULT_BILLING_START_MONTH,
                        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
                    }
                ),
                self.config_entry.options,
            ),
        )
