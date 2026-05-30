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
from .auth import AuthError, SamsungAccountAuth, SmartThingsOAuth
from .auth import SAMSUNG_LOGIN_CLIENT_ID
from .const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PAT,
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
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
                menu_options=["oauth", "standalone_oauth", "pat"],
            )
        # No HA core smartthings entry → offer standalone OAuth or PAT
        return self.async_show_menu(
            step_id="user",
            menu_options=["standalone_oauth", "pat"],
        )

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

    # ---------------- Standalone OAuth path ----------------

    async def async_step_standalone_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Bridge: HA resolves menu option 'standalone_oauth' to this method."""
        return await self.async_step_standalone_oauth_credentials(user_input)

    async def async_step_standalone_oauth_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 of standalone OAuth: collect client_id and client_secret."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client_id = user_input.get(CONF_OAUTH_CLIENT_ID, "").strip()
            client_secret = user_input.get(CONF_OAUTH_CLIENT_SECRET, "").strip()
            if not client_id:
                errors[CONF_OAUTH_CLIENT_ID] = "required"
            elif not client_secret:
                errors[CONF_OAUTH_CLIENT_SECRET] = "required"
            else:
                oauth = SmartThingsOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                )
                self._standalone_oauth = oauth
                self._standalone_client_id = client_id
                self._standalone_client_secret = client_secret
                self._standalone_auth_url = oauth.get_authorization_url()
                return await self.async_step_standalone_oauth_link()

        schema = vol.Schema(
            {
                vol.Required(CONF_OAUTH_CLIENT_ID): str,
                vol.Required(CONF_OAUTH_CLIENT_SECRET): str,
            }
        )
        return self.async_show_form(
            step_id="standalone_oauth_credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_standalone_oauth_link(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Step 2 of standalone OAuth: display auth URL, collect redirect or code."""
        errors: dict[str, str] = {}
        oauth: SmartThingsOAuth | None = getattr(self, "_standalone_oauth", None)
        if oauth is None:
            return await self.async_step_standalone_oauth_credentials()

        auth_url: str = getattr(self, "_standalone_auth_url", "") or oauth.get_authorization_url()

        if user_input is not None:
            raw = user_input.get("redirect_url_or_code", "").strip()
            if not raw:
                errors["redirect_url_or_code"] = "required"
            else:
                try:
                    if raw.startswith("http"):
                        code = SmartThingsOAuth.extract_code_from_redirect(raw)
                    else:
                        code = raw
                    creds = await self.hass.async_add_executor_job(oauth.exchange_code, code)
                except ValueError:
                    errors["redirect_url_or_code"] = "invalid_redirect_url"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Code exchange failed")
                    errors["redirect_url_or_code"] = "code_exchange_failed"
                else:
                    self._standalone_access_token = creds.access_token
                    self._standalone_refresh_token = creds.refresh_token
                    return await self.async_step_standalone_oauth_samsung()

        schema = vol.Schema({"redirect_url_or_code": str})
        return self.async_show_form(
            step_id="standalone_oauth_link",
            data_schema=schema,
            errors=errors,
            description_placeholders={"authorization_url": auth_url},
        )

    async def async_step_standalone_oauth_samsung(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3 of standalone OAuth: optional Samsung Account login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = (user_input.get("samsung_email") or "").strip()
            password = (user_input.get("samsung_password") or "").strip()
            data: dict[str, Any] = {
                CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
                CONF_OAUTH_CLIENT_ID: self._standalone_client_id,
                CONF_OAUTH_CLIENT_SECRET: self._standalone_client_secret,
                CONF_OAUTH_REFRESH_TOKEN: self._standalone_refresh_token,
            }
            if email and password:
                try:
                    samsung_auth = SamsungAccountAuth(
                        email=email,
                        password=password,
                        signin_client_id=SAMSUNG_LOGIN_CLIENT_ID,
                        signin_client_secret="",
                    )
                    iot_creds = await self.hass.async_add_executor_job(
                        samsung_auth.login_iot
                    )
                    data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] = iot_creds.refresh_token
                    data[CONF_SAMSUNG_IOT_AUTH_SERVER] = iot_creds.auth_server_url
                except AuthError as err:
                    _LOGGER.warning("Samsung Account login failed: %s", err)
                    errors["base"] = "samsung_login_failed"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error during Samsung login")
                    errors["base"] = "unknown"
            else:
                _LOGGER.warning(
                    "Samsung Account credentials not provided — IoT features unavailable"
                )

            if not errors:
                return self.async_create_entry(
                    title="Samsung Fridge Camera (Standalone OAuth)", data=data
                )

        schema = vol.Schema(
            {
                vol.Optional("samsung_email"): str,
                vol.Optional("samsung_password"): str,
            }
        )
        return self.async_show_form(
            step_id="standalone_oauth_samsung",
            data_schema=schema,
            errors=errors,
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
        auth_mode = entry_data.get(CONF_AUTH_MODE)
        if auth_mode == AUTH_MODE_OAUTH:
            # OAuth-mode entries should never reach reauth: HA's OAuth2Session
            # refreshes transparently. If we land here anyway, offer full re-setup.
            return await self.async_step_user()
        if auth_mode == AUTH_MODE_STANDALONE_OAUTH:
            return await self.async_step_reauth_standalone_oauth()
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

    async def async_step_reauth_standalone_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter standalone OAuth client credentials and repeat the auth flow."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id = user_input.get(CONF_OAUTH_CLIENT_ID, "").strip()
            client_secret = user_input.get(CONF_OAUTH_CLIENT_SECRET, "").strip()
            if not client_id:
                errors[CONF_OAUTH_CLIENT_ID] = "required"
            elif not client_secret:
                errors[CONF_OAUTH_CLIENT_SECRET] = "required"
            else:
                oauth = SmartThingsOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                )
                self._standalone_oauth = oauth
                self._standalone_client_id = client_id
                self._standalone_client_secret = client_secret
                self._standalone_auth_url = oauth.get_authorization_url()
                self._reauth_entry = reauth_entry
                return await self.async_step_reauth_standalone_oauth_link()

        # Pre-fill client_id from existing entry so the user only needs to
        # re-enter the secret (or replace both if they created a new app).
        existing_client_id = reauth_entry.data.get(CONF_OAUTH_CLIENT_ID, "")
        schema = vol.Schema(
            {
                vol.Required(CONF_OAUTH_CLIENT_ID, default=existing_client_id): str,
                vol.Required(CONF_OAUTH_CLIENT_SECRET): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_standalone_oauth",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth_standalone_oauth_link(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect redirect URL or raw auth code during standalone OAuth reauth."""
        errors: dict[str, str] = {}
        oauth: SmartThingsOAuth | None = getattr(self, "_standalone_oauth", None)
        if oauth is None:
            return await self.async_step_reauth_standalone_oauth()

        auth_url: str = getattr(self, "_standalone_auth_url", "") or oauth.get_authorization_url()
        reauth_entry = getattr(self, "_reauth_entry", None) or self._get_reauth_entry()

        if user_input is not None:
            raw = user_input.get("redirect_url_or_code", "").strip()
            if not raw:
                errors["redirect_url_or_code"] = "required"
            else:
                try:
                    if raw.startswith("http"):
                        code = SmartThingsOAuth.extract_code_from_redirect(raw)
                    else:
                        code = raw
                    creds = await self.hass.async_add_executor_job(oauth.exchange_code, code)
                except ValueError:
                    errors["redirect_url_or_code"] = "invalid_redirect_url"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Code exchange failed during reauth")
                    errors["redirect_url_or_code"] = "code_exchange_failed"
                else:
                    new_data = {
                        **reauth_entry.data,
                        CONF_OAUTH_CLIENT_ID: self._standalone_client_id,
                        CONF_OAUTH_CLIENT_SECRET: self._standalone_client_secret,
                        CONF_OAUTH_REFRESH_TOKEN: creds.refresh_token,
                        CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
                    }
                    return self.async_update_reload_and_abort(
                        reauth_entry, data=new_data
                    )

        schema = vol.Schema({"redirect_url_or_code": str})
        return self.async_show_form(
            step_id="reauth_standalone_oauth_link",
            data_schema=schema,
            errors=errors,
            description_placeholders={"authorization_url": auth_url},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
