"""!
@brief Config flow for CondorSync Home Assistant integration.
@details Implements initial user setup, MFA verification, and the official re-authentication (reauth) flow.
@note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, ADR-172, ADR-173
@author Dennis Braun
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

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
    CONF_API_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

## @brief Schema for permanent API token entry.
STEP_TOKEN_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
    }
)

## @brief Schema strictly containing only Email and Password.
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

## @brief Schema for MFA verification step containing the TOTP code.
STEP_MFA_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """!
    @brief Handle a config flow and re-authentication for CondorSync integration.
    @note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, REQ-HA-PERM-001, ADR-172, ADR-173, ADR-174
    @author Dennis Braun
    """

    VERSION = 1

    def __init__(self) -> None:
        """!
        @brief Initialize config flow state.
        @author Dennis Braun
        """
        self._user_input: dict[str, Any] = {}
        self._mfa_token_temp: str | None = None
        self._mfa_type: str | None = None
        self._has_trusted_device: bool = False
        self._api: CondorSyncAPI | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """!
        @brief Handle the initial setup step by showing auth options or dispatching direct input.
        @param user_input Optional dictionary if submitted directly.
        @return FlowResult showing menu options or next step.
        @note Relates to REQ-HA-SYNC-001, REQ-HA-PERM-001, ADR-174
        @author Dennis Braun
        """
        if user_input is not None:
            if CONF_API_TOKEN in user_input:
                return await self.async_step_token(user_input)
            if CONF_EMAIL in user_input:
                return await self.async_step_credentials(user_input)

        return self.async_show_menu(
            step_id="user",
            menu_options=["token", "credentials"],
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """!
        @brief Handle setup via permanent API token (Recommended for 24/7 Home Assistant).
        @param user_input Dictionary containing api_token if submitted.
        @return FlowResult showing form or creating entry.
        @note Relates to REQ-HA-PERM-001, ADR-174
        @author Dennis Braun
        """
        if user_input is None:
            return self.async_show_form(
                step_id="token", data_schema=STEP_TOKEN_DATA_SCHEMA
            )

        errors = {}
        api_token = user_input[CONF_API_TOKEN].strip()

        api = CondorSyncAPI(
            api_token=api_token,
            api_url=DEFAULT_API_URL,
        )

        user_info = await api.get_current_user()
        if user_info and user_info.get("id"):
            email = user_info.get("email", "condorsync").strip().lower()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()
            user_level = api.user_level
            await api.close()
            return self.async_create_entry(
                title=email,
                data={
                    CONF_API_TOKEN: api_token,
                    CONF_EMAIL: email,
                    CONF_API_URL: DEFAULT_API_URL,
                    CONF_USER_LEVEL: user_level,
                },
            )

        await api.close()
        errors["base"] = "invalid_auth"
        return self.async_show_form(
            step_id="token", data_schema=STEP_TOKEN_DATA_SCHEMA, errors=errors
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """!
        @brief Handle setup via Email and Password with automatic permanent token acquisition.
        @param user_input Dictionary containing email and password if submitted.
        @return FlowResult showing next step or creating entry.
        @note Relates to REQ-HA-SYNC-001, REQ-HA-PERM-001, ADR-172, ADR-174
        @author Dennis Braun
        """
        if user_input is None:
            return self.async_show_form(
                step_id="credentials", data_schema=STEP_USER_DATA_SCHEMA
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
            perm_token = await api.get_permanent_token()
            await api.close()
            entry_data = {
                CONF_EMAIL: email,
                CONF_PASSWORD: password,
                CONF_API_URL: DEFAULT_API_URL,
                CONF_DEVICE_ID: api.device_id,
                CONF_ACCESS_TOKEN: login_result.get("access_token"),
                CONF_REFRESH_TOKEN: login_result.get("refresh_token"),
                CONF_USER_LEVEL: user_level,
            }
            if perm_token:
                entry_data[CONF_API_TOKEN] = perm_token

            return self.async_create_entry(
                title=email,
                data=entry_data,
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
            step_id="credentials", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """!
        @brief Handle initiation of re-authentication from Home Assistant.
        @param entry_data Existing configuration entry data mapping.
        @return FlowResult transitioning to reauth confirmation form.
        @note Relates to REQ-HA-REAUTH-001, ADR-173
        @author Dennis Braun
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """!
        @brief Confirm re-authentication with API token or password and MFA challenge.
        @param user_input Submitted user credentials or API token.
        @return FlowResult continuing to MFA or completing reauth.
        @note Relates to REQ-HA-REAUTH-001, REQ-HA-PERM-001, ADR-173, ADR-174
        @author Dennis Braun
        """
        errors = {}
        if not self._reauth_entry:
            return self.async_abort(reason="reauth_failed")

        email = self._reauth_entry.data.get(CONF_EMAIL, "CondorSync")

        # If entry was configured with an API token, prompt for API token renewal
        if self._reauth_entry.data.get(CONF_API_TOKEN):
            if user_input is None:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=STEP_TOKEN_DATA_SCHEMA,
                    description_placeholders={"email": email},
                )

            token_val = user_input.get(CONF_API_TOKEN, "").strip()
            api = CondorSyncAPI(
                api_token=token_val,
                api_url=self._reauth_entry.data.get(CONF_API_URL, DEFAULT_API_URL),
            )
            user_info = await api.get_current_user()
            if user_info and user_info.get("id"):
                user_level = api.user_level
                await api.close()
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_API_TOKEN: token_val,
                        CONF_USER_LEVEL: user_level,
                    },
                )
            await api.close()
            errors["base"] = "invalid_auth"
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=STEP_TOKEN_DATA_SCHEMA,
                errors=errors,
                description_placeholders={"email": email},
            )

        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({
                    vol.Required(CONF_PASSWORD): str,
                }),
                description_placeholders={"email": email},
            )

        password = user_input[CONF_PASSWORD]
        api = CondorSyncAPI(
            email=email,
            password=password,
            api_url=self._reauth_entry.data.get(CONF_API_URL, DEFAULT_API_URL),
            device_id=self._reauth_entry.data.get(CONF_DEVICE_ID),
        )

        login_result = await api.login()
        status = login_result.get("status")

        if status == "success":
            user_level = api.user_level
            perm_token = await api.get_permanent_token()
            await api.close()
            new_data = {
                **self._reauth_entry.data,
                CONF_PASSWORD: password,
                CONF_ACCESS_TOKEN: login_result.get("access_token"),
                CONF_REFRESH_TOKEN: login_result.get("refresh_token"),
                CONF_USER_LEVEL: user_level,
            }
            if perm_token:
                new_data[CONF_API_TOKEN] = perm_token

            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data=new_data,
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
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={"email": email},
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """!
        @brief Handle MFA verification code step for initial setup and re-authentication.
        @param user_input Dictionary containing MFA code if submitted.
        @return FlowResult creating entry or reloading existing entry.
        @note Relates to REQ-HA-SYNC-001, REQ-HA-REAUTH-001, REQ-HA-PERM-001, ADR-172, ADR-173, ADR-174
        @author Dennis Braun
        """
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
                perm_token = await self._api.get_permanent_token()
                entry_data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                    CONF_API_URL: DEFAULT_API_URL,
                    CONF_DEVICE_ID: self._api.device_id,
                    CONF_ACCESS_TOKEN: verify_result.get("access_token"),
                    CONF_REFRESH_TOKEN: verify_result.get("refresh_token"),
                    CONF_USER_LEVEL: user_level,
                }
                if perm_token:
                    entry_data[CONF_API_TOKEN] = perm_token
                await self._api.close()

                if self._reauth_entry:
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data={
                            **self._reauth_entry.data,
                            **entry_data,
                        },
                    )

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
    """!
    @brief Error to indicate there is invalid auth.
    @author Dennis Braun
    """


class CannotConnect(HomeAssistantError):
    """!
    @brief Error to indicate communication failure.
    @author Dennis Braun
    """
