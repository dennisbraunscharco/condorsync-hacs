"""Config flow for CondorSync integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import CondorSyncAPI
from .const import (
    DOMAIN,
    CONF_API_URL,
    DEFAULT_API_URL,
    CONF_DEVICE_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_USER_LEVEL,
)

_LOGGER = logging.getLogger(__name__)

# Schema strictly containing only Email and Password.
# API URL is fixed to DEFAULT_API_URL (https://condorsync.de/api) and not editable.
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_MFA_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CondorSync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow state."""
        self._user_input: dict[str, Any] = {}
        self._mfa_token_temp: str | None = None
        self._mfa_type: str | None = None
        self._has_trusted_device: bool = False
        self._api: CondorSyncAPI | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user login step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}
        email = user_input[CONF_EMAIL].strip().lower()
        password = user_input[CONF_PASSWORD]

        await self.async_set_unique_id(email)
        self._abort_if_unique_id_configured()

        api = CondorSyncAPI(
            email=email,
            password=password,
            api_url=DEFAULT_API_URL,
        )

        login_result = await api.login()
        status = login_result.get("status")

        if status == "success":
            user_level = api.user_level
            await api.close()
            return self.async_create_entry(
                title=email,
                data={
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                    CONF_API_URL: DEFAULT_API_URL,
                    CONF_DEVICE_ID: api.device_id,
                    CONF_ACCESS_TOKEN: login_result.get("access_token"),
                    CONF_REFRESH_TOKEN: login_result.get("refresh_token"),
                    CONF_USER_LEVEL: user_level,
                },
            )

        if status == "mfa_required":
            self._user_input = {CONF_EMAIL: email, CONF_PASSWORD: password}
            self._mfa_token_temp = login_result.get("mfa_token_temp")
            self._mfa_type = login_result.get("mfa_type", "totp")
            self._has_trusted_device = login_result.get("has_trusted_device", False)
            self._api = api

            # Trigger push notification to smartphone if user has trusted device
            if self._has_trusted_device and self._mfa_token_temp:
                try:
                    await api.request_mfa_push(self._mfa_token_temp)
                except Exception as push_err:
                    _LOGGER.warning("Could not send MFA push notification: %s", push_err)

            return await self.async_step_mfa()

        await api.close()

        if status == "invalid_auth":
            errors["base"] = "invalid_auth"
        elif status == "connection_error":
            errors["base"] = "cannot_connect"
        else:
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle MFA verification code step."""
        errors = {}

        if user_input is not None and self._api and self._mfa_token_temp:
            code = user_input.get("code", "").strip()
            verify_result = await self._api.verify_mfa(
                mfa_token_temp=self._mfa_token_temp,
                code=code,
            )

            if verify_result.get("status") == "success":
                email = self._user_input[CONF_EMAIL]
                user_level = self._api.user_level
                entry_data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                    CONF_API_URL: DEFAULT_API_URL,
                    CONF_DEVICE_ID: self._api.device_id,
                    CONF_ACCESS_TOKEN: verify_result.get("access_token"),
                    CONF_REFRESH_TOKEN: verify_result.get("refresh_token"),
                    CONF_USER_LEVEL: user_level,
                }
                await self._api.close()
                return self.async_create_entry(title=email, data=entry_data)

            if verify_result.get("status") == "invalid_code":
                errors["base"] = "invalid_mfa_code"
            elif verify_result.get("status") == "connection_error":
                errors["base"] = "cannot_connect"
            else:
                errors["base"] = "mfa_failed"

        description_placeholders = {
            "email": self._user_input.get(CONF_EMAIL, ""),
        }

        return self.async_show_form(
            step_id="mfa",
            data_schema=STEP_MFA_DATA_SCHEMA,
            errors=errors,
            description_placeholders=description_placeholders,
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class CannotConnect(HomeAssistantError):
    """Error to indicate communication failure."""
