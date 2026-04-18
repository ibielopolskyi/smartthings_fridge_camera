"""Config flow for Samsung FamilyHub Fridge integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .api import AuthenticationError, FamilyHub
from .const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PAT,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_TOKEN,
    DOMAIN,
    SMARTTHINGS_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


STEP_PAT_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_DEVICE_ID): str,
    }
)


async def _validate_pat(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate a Personal Access Token and resolve the device ID."""
    hub = FamilyHub(hass, data[CONF_TOKEN], data.get(CONF_DEVICE_ID))

    try:
        if not await hub.authenticate():
            raise InvalidAuth
    except AuthenticationError as err:
        raise InvalidAuth from err

    if not data.get(CONF_DEVICE_ID):
        data[CONF_DEVICE_ID] = hub.device_id

    return data


async def _validate_oauth(
    hass: HomeAssistant, smartthings_entry_id: str, device_id: str | None
) -> dict[str, Any]:
    """Validate that we can borrow the SmartThings OAuth session, probe the fridge."""
    smartthings_entry = hass.config_entries.async_get_entry(smartthings_entry_id)
    if smartthings_entry is None or smartthings_entry.domain != SMARTTHINGS_DOMAIN:
        raise CannotConnect(
            f"Linked SmartThings entry {smartthings_entry_id} not found"
        )

    impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, smartthings_entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, smartthings_entry, impl)
    try:
        await session.async_ensure_token_valid()
    except Exception as err:  # pylint: disable=broad-except
        raise InvalidAuth from err

    token = session.token["access_token"]
    hub = FamilyHub(hass, token=token, device_id=device_id)
    hub.attach_oauth_session(session)

    try:
        if not await hub.authenticate():
            raise InvalidAuth
    except AuthenticationError as err:
        raise InvalidAuth from err

    return {
        CONF_AUTH_MODE: AUTH_MODE_OAUTH,
        CONF_LINKED_SMARTTHINGS_ENTRY_ID: smartthings_entry_id,
        CONF_DEVICE_ID: device_id or hub.device_id,
    }


def _smartthings_entries(hass: HomeAssistant) -> list[config_entries.ConfigEntry]:
    """Return all loaded HA core smartthings config entries."""
    return [
        e
        for e in hass.config_entries.async_entries(SMARTTHINGS_DOMAIN)
        if e.source != config_entries.SOURCE_IGNORE
    ]


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Samsung FamilyHub Fridge."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step — offer OAuth reuse if a smartthings entry exists."""
        if _smartthings_entries(self.hass):
            return self.async_show_menu(
                step_id="user",
                menu_options=["oauth", "pat"],
            )
        # No HA core smartthings entry → force PAT path
        return await self.async_step_pat()

    # ---------------- OAuth path ----------------

    async def async_step_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer to reuse an existing HA core SmartThings OAuth entry."""
        entries = _smartthings_entries(self.hass)
        if not entries:
            return await self.async_step_pat()

        errors: dict[str, str] = {}
        options = {e.entry_id: e.title or e.entry_id for e in entries}

        if user_input is not None:
            try:
                data = await _validate_oauth(
                    self.hass,
                    user_input[CONF_LINKED_SMARTTHINGS_ENTRY_ID],
                    user_input.get(CONF_DEVICE_ID) or None,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during OAuth validation")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Samsung Fridge Camera (OAuth)", data=data
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_LINKED_SMARTTHINGS_ENTRY_ID): vol.In(options),
                vol.Optional(CONF_DEVICE_ID): str,
            }
        )
        return self.async_show_form(
            step_id="oauth", data_schema=schema, errors=errors
        )

    # ---------------- PAT path (legacy) ----------------

    async def async_step_pat(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enter a raw SmartThings Personal Access Token (legacy, 24h expiry)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await _validate_pat(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                data = {**info, CONF_AUTH_MODE: AUTH_MODE_PAT}
                return self.async_create_entry(
                    title="Samsung Fridge Camera", data=data
                )

        return self.async_show_form(
            step_id="pat", data_schema=STEP_PAT_DATA_SCHEMA, errors=errors
        )

    # ---------------- Reauth ----------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Handle re-authentication when the token has expired."""
        # OAuth-mode entries should never reach reauth: HA's OAuth2Session
        # refreshes transparently and any hard failure is surfaced on the
        # linked smartthings entry itself. If we do land here, offer both
        # paths again so the user can re-link.
        if entry_data.get(CONF_AUTH_MODE) == AUTH_MODE_OAUTH:
            return await self.async_step_user()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle PAT re-authentication."""
        errors: dict[str, str] = {}
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            new_data = {
                **reauth_entry.data,
                CONF_TOKEN: user_input[CONF_TOKEN],
                CONF_AUTH_MODE: AUTH_MODE_PAT,
            }
            try:
                await _validate_pat(self.hass, new_data)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during re-auth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=new_data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
