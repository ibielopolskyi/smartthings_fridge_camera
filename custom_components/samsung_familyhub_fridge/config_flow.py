"""Config flow for Samsung FamilyHub Fridge integration."""
from __future__ import annotations

import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .api import DEVICES_URL, AuthenticationError, FamilyHub
from .auth import AuthError, SamsungAccountAuth, SmartThingsOAuth
from .auth import SAMSUNG_LOGIN_CLIENT_ID
from .samsung_browser_login import SamsungBrowserLogin
from .const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PAT,
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_IMAGE_TOKEN,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
    CONF_SAMSUNG_LOGIN_ID,
    CONF_SAMSUNG_PHYSICAL_ADDRESS,
    CONF_SAMSUNG_USERAUTH_TOKEN,
    CONF_SAMSUNG_SIGNIN_CLIENT_SECRET,
    CONF_TOKEN,
    DOMAIN,
    SMARTTHINGS_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


# Temporary key carrying {device_id: name} from validation to the flow.
FRIDGES_KEY = "_fridges"

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

    data[FRIDGES_KEY] = hub.list_fridges()
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
        CONF_DEVICE_ID: device_id,
        FRIDGES_KEY: hub.list_fridges(),
    }


async def _list_fridges_with_token(hass: HomeAssistant, token: str) -> dict[str, str]:
    """Fridges visible to a SmartThings access token (empty on any error)."""
    hub = FamilyHub(hass, token=token, device_id=None)
    try:
        await hub.authenticate()
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.warning("Could not list SmartThings fridges: %s", err)
        return {}
    return hub.list_fridges()


class SamsungLoginFailed(HomeAssistantError):
    """Samsung Account login failed (bad credentials, 2FA, ...)."""


class SamsungTwoFactorRequired(SamsungLoginFailed):
    """Samsung accepted the password but wants a 2-step verification code."""


async def _samsung_iot_login(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Optionally log in to Samsung Account for camera image downloads.

    Returns config-entry data to merge in: the IoT refresh token and auth
    server when email + password were given, otherwise an empty dict.
    """
    email = (user_input.get("samsung_email") or "").strip()
    password = (user_input.get("samsung_password") or "").strip()
    signin_client_secret = (
        user_input.get(CONF_SAMSUNG_SIGNIN_CLIENT_SECRET) or ""
    ).strip()
    data: dict[str, Any] = {CONF_SAMSUNG_SIGNIN_CLIENT_SECRET: signin_client_secret}
    if not (email and password):
        if not (user_input.get(CONF_IMAGE_TOKEN) or "").strip():
            _LOGGER.info(
                "No picture token or Samsung Account login given — camera "
                "pictures will only work if the public SmartThings files "
                "endpoint accepts this login"
            )
        return data
    samsung_auth = SamsungAccountAuth(
        email=email,
        password=password,
        signin_client_id=SAMSUNG_LOGIN_CLIENT_ID,
        signin_client_secret=signin_client_secret,
    )
    try:
        iot_creds = await hass.async_add_executor_job(samsung_auth.login_iot)
    except (AuthError, requests.RequestException) as err:
        _LOGGER.warning(
            "Samsung Account login failed%s: %s",
            "" if signin_client_secret else " (no signin client secret given)",
            err,
        )
        if "2factor" in str(err).lower() or "2-step" in str(err).lower():
            raise SamsungTwoFactorRequired from err
        raise SamsungLoginFailed from err
    data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] = iot_creds.refresh_token
    data[CONF_SAMSUNG_IOT_AUTH_SERVER] = iot_creds.auth_server_url
    data.update(
        _samsung_backup_data(
            getattr(samsung_auth, "userauth_token", None),
            email,
            getattr(samsung_auth, "_device_id", None),
        )
    )
    return data


def _samsung_backup_data(
    userauth_token: str | None, login_id: str | None, physical_address: str | None
) -> dict[str, Any]:
    """Entry data for renewing the IoT token if its refresh token is refused."""
    if not isinstance(userauth_token, str) or not userauth_token:
        return {}
    return {
        CONF_SAMSUNG_USERAUTH_TOKEN: userauth_token,
        CONF_SAMSUNG_LOGIN_ID: login_id or "",
        CONF_SAMSUNG_PHYSICAL_ADDRESS: physical_address or "",
    }


class InvalidImageToken(HomeAssistantError):
    """The picture token was rejected by SmartThings."""


async def _validate_image_token(hass: HomeAssistant, token: str) -> None:
    """Check a picture token is a working SmartThings token."""

    def _check() -> int:
        return requests.get(
            DEVICES_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).status_code

    try:
        status = await hass.async_add_executor_job(_check)
    except requests.RequestException as err:
        raise CannotConnect from err
    if status in (401, 403):
        raise InvalidImageToken


async def _image_token_data(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> dict[str, Any]:
    token = (user_input.get(CONF_IMAGE_TOKEN) or "").strip()
    if not token:
        return {}
    await _validate_image_token(hass, token)
    return {CONF_IMAGE_TOKEN: token}


IMAGE_TOKEN_FIELD = {vol.Optional(CONF_IMAGE_TOKEN): str}

CONF_SAMSUNG_BROWSER_LOGIN = "samsung_browser_login"
BROWSER_LOGIN_FIELD = {vol.Optional(CONF_SAMSUNG_BROWSER_LOGIN, default=False): bool}

SAMSUNG_FIELDS = {
    vol.Optional("samsung_email"): str,
    vol.Optional("samsung_password"): str,
    vol.Optional(CONF_SAMSUNG_SIGNIN_CLIENT_SECRET): str,
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
                    (user_input.get(CONF_DEVICE_ID) or "").strip() or None,
                )
                data.update(await _image_token_data(self.hass, user_input))
                if not user_input.get(CONF_SAMSUNG_BROWSER_LOGIN):
                    data.update(await _samsung_iot_login(self.hass, user_input))
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidImageToken:
                errors[CONF_IMAGE_TOKEN] = "invalid_image_token"
            except SamsungTwoFactorRequired:
                errors["base"] = "samsung_2fa_required"
            except SamsungLoginFailed:
                errors["base"] = "samsung_login_failed"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during OAuth validation")
                errors["base"] = "unknown"
            if not errors:
                self._remember_fridges(data)
                result = await self._finish(
                    "Samsung Fridge Camera (OAuth)",
                    data,
                    browser=bool(user_input.get(CONF_SAMSUNG_BROWSER_LOGIN)),
                )
                if result is not None:
                    return result
                errors["base"] = "no_device_found"

        schema = vol.Schema(
            {
                vol.Required(CONF_LINKED_SMARTTHINGS_ENTRY_ID): vol.In(options),
                vol.Optional(CONF_DEVICE_ID): str,
                **BROWSER_LOGIN_FIELD,
                **IMAGE_TOKEN_FIELD,
                **SAMSUNG_FIELDS,
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
                    self._fridges = await _list_fridges_with_token(
                        self.hass, creds.access_token
                    )
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
            data: dict[str, Any] = {
                CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
                CONF_OAUTH_CLIENT_ID: self._standalone_client_id,
                CONF_OAUTH_CLIENT_SECRET: self._standalone_client_secret,
                CONF_OAUTH_REFRESH_TOKEN: self._standalone_refresh_token,
            }
            try:
                data.update(await _image_token_data(self.hass, user_input))
                if not user_input.get(CONF_SAMSUNG_BROWSER_LOGIN):
                    data.update(await _samsung_iot_login(self.hass, user_input))
            except InvalidImageToken:
                errors[CONF_IMAGE_TOKEN] = "invalid_image_token"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except SamsungTwoFactorRequired:
                errors["base"] = "samsung_2fa_required"
            except SamsungLoginFailed:
                errors["base"] = "samsung_login_failed"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during Samsung login")
                errors["base"] = "unknown"

            if not errors:
                # If the fridge list couldn't be read, keep the old behaviour:
                # the fridge is auto-detected when the integration starts.
                result = await self._finish(
                    "Samsung Fridge Camera (Standalone OAuth)",
                    data,
                    browser=bool(user_input.get(CONF_SAMSUNG_BROWSER_LOGIN)),
                    allow_no_device=not getattr(self, "_fridges", None),
                )
                if result is not None:
                    return result
                errors["base"] = "no_device_found"

        schema = vol.Schema(
            {**BROWSER_LOGIN_FIELD, **IMAGE_TOKEN_FIELD, **SAMSUNG_FIELDS}
        )
        return self.async_show_form(
            step_id="standalone_oauth_samsung",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "samsung_signin_client_secret_hint": (
                    "Obtain from SmartThings APK decompilation or open-source mirrors "
                    "(search for 'signin_client_secret' in the SmartThings app source). "
                    "Leave empty to skip Samsung IoT token acquisition."
                )
            },
        )

    # ---------------- Choosing the fridge ----------------

    def _remember_fridges(self, data: dict[str, Any]) -> None:
        """Move the validator's fridge list out of the entry data."""
        fridges = data.pop(FRIDGES_KEY, None)
        if fridges is not None:
            self._fridges = fridges

    def _configured_device_ids(self) -> set[str]:
        ids = set()
        for entry in self._async_current_entries():
            device_id = entry.data.get(CONF_DEVICE_ID)
            if isinstance(device_id, str) and device_id:
                ids.add(device_id)
        return ids

    async def _finish(
        self,
        title: str,
        data: dict[str, Any],
        browser: bool = False,
        allow_no_device: bool = False,
    ) -> FlowResult | None:
        """Pick the fridge (asking if there are several), then create.

        Returns None when the account has no fridge, so the caller can show
        ``no_device_found`` on its form.
        """
        if not data.get(CONF_DEVICE_ID):
            fridges = getattr(self, "_fridges", None) or {}
            if not fridges:
                if allow_no_device:
                    return await self._create(title, data, browser)
                return None
            configured = self._configured_device_ids()
            available = {k: v for k, v in fridges.items() if k not in configured}
            if not available:
                return self.async_abort(reason="already_configured")
            if len(available) > 1:
                self._pending_pick = (title, data, browser, available)
                return await self.async_step_pick_fridge()
            data[CONF_DEVICE_ID] = next(iter(available))
        return await self._create(title, data, browser)

    async def _create(
        self, title: str, data: dict[str, Any], browser: bool
    ) -> FlowResult:
        """Create the entry for the chosen fridge (one entry per fridge)."""
        device_id = data.get(CONF_DEVICE_ID)
        if device_id:
            if device_id in self._configured_device_ids():
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured()
            name = (getattr(self, "_fridges", None) or {}).get(device_id)
            # Entries with a fridge name get entity names from it.
            data[CONF_DEVICE_NAME] = name or "Samsung Family Hub Fridge"
            if name and name != device_id:
                title = name
        if browser:
            self._pending_entry = (title, data)
            return await self.async_step_samsung_browser()
        return self.async_create_entry(title=title, data=data)

    async def async_step_pick_fridge(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Several fridges in the account: let the user pick one."""
        pending = getattr(self, "_pending_pick", None)
        if pending is None:
            return await self.async_step_user()
        title, data, browser, available = pending
        if user_input is not None and user_input.get(CONF_DEVICE_ID) in available:
            self._pending_pick = None
            data = {**data, CONF_DEVICE_ID: user_input[CONF_DEVICE_ID]}
            return await self._create(title, data, browser)
        return self.async_show_form(
            step_id="pick_fridge",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): vol.In(available)}
            ),
        )

    # ---------------- Samsung sign-in via Samsung's web page ----------------

    async def async_step_samsung_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Sign in on Samsung's own page (handles 2-step verification)."""
        errors: dict[str, str] = {}
        pending = getattr(self, "_pending_entry", None)
        if pending is None:
            return await self.async_step_user()
        title, data = pending

        login: SamsungBrowserLogin | None = getattr(self, "_browser_login", None)
        if login is None:
            ha_config = getattr(self.hass, "config", None)
            country = (getattr(ha_config, "country", None) or "us").lower()
            language = (getattr(ha_config, "language", None) or "en")[:2]
            login = SamsungBrowserLogin(country=country, language=language)
            try:
                self._browser_url = await self.hass.async_add_executor_job(
                    login.start
                )
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning("Could not start Samsung sign-in: %s", err)
                return self.async_abort(reason="samsung_browser_unavailable")
            self._browser_login = login

        if user_input is not None:
            redirect = (user_input.get("redirect_url") or "").strip()
            if not redirect:
                errors["redirect_url"] = "required"
            else:
                try:
                    creds = await self.hass.async_add_executor_job(
                        login.finish, redirect
                    )
                except ValueError as err:
                    _LOGGER.warning("Samsung sign-in address not usable: %s", err)
                    errors["redirect_url"] = "invalid_samsung_redirect"
                except (AuthError, requests.RequestException) as err:
                    _LOGGER.warning("Samsung sign-in failed: %s", err)
                    errors["base"] = "samsung_browser_failed"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error finishing Samsung sign-in")
                    errors["base"] = "unknown"
                else:
                    data = {
                        **data,
                        CONF_SAMSUNG_IOT_REFRESH_TOKEN: creds.refresh_token,
                        CONF_SAMSUNG_IOT_AUTH_SERVER: creds.auth_server_url,
                        **_samsung_backup_data(
                            getattr(login, "userauth_token", None),
                            getattr(login, "username", None),
                            getattr(login, "device_id", None),
                        ),
                    }
                    self._pending_entry = None
                    return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="samsung_browser",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            errors=errors,
            description_placeholders={"sign_in_url": self._browser_url},
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
                self._remember_fridges(data)
                result = await self._finish("Samsung Fridge Camera", data)
                if result is not None:
                    return result
                errors["base"] = "no_device_found"

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
        # A picture token that expired is the common reauth cause for OAuth
        # modes: ask for a new one rather than redoing the whole login.
        if entry_data.get(CONF_IMAGE_TOKEN) and entry_data.get(CONF_AUTH_MODE) in (
            AUTH_MODE_OAUTH,
            AUTH_MODE_STANDALONE_OAUTH,
        ):
            return await self.async_step_reauth_image_token()
        if entry_data.get(CONF_AUTH_MODE) == AUTH_MODE_OAUTH:
            return await self.async_step_user()
        if entry_data.get(CONF_AUTH_MODE) == AUTH_MODE_STANDALONE_OAUTH:
            return await self.async_step_reauth_standalone_oauth()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_image_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a new picture token (SmartThings PATs last 24 hours)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = (user_input.get(CONF_IMAGE_TOKEN) or "").strip()
            if not token:
                errors[CONF_IMAGE_TOKEN] = "required"
            else:
                try:
                    await _validate_image_token(self.hass, token)
                except InvalidImageToken:
                    errors[CONF_IMAGE_TOKEN] = "invalid_image_token"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                else:
                    reauth_entry = self._get_reauth_entry()
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data={**reauth_entry.data, CONF_IMAGE_TOKEN: token},
                    )

        return self.async_show_form(
            step_id="reauth_image_token",
            data_schema=vol.Schema({vol.Required(CONF_IMAGE_TOKEN): str}),
            errors=errors,
        )

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
                new_data.pop(FRIDGES_KEY, None)
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
        """Step 1 of standalone OAuth re-auth: collect client credentials."""
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
                    client_id=client_id, client_secret=client_secret
                )
                self._reauth_standalone_oauth = oauth
                self._reauth_standalone_client_id = client_id
                self._reauth_standalone_client_secret = client_secret
                self._reauth_standalone_auth_url = oauth.get_authorization_url()
                return await self.async_step_reauth_standalone_oauth_link()

        schema = vol.Schema(
            {
                vol.Required(CONF_OAUTH_CLIENT_ID): str,
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
        """Step 2 of standalone OAuth re-auth: display auth URL, collect code."""
        errors: dict[str, str] = {}
        oauth: SmartThingsOAuth | None = getattr(
            self, "_reauth_standalone_oauth", None
        )
        if oauth is None:
            return await self.async_step_reauth_standalone_oauth()

        auth_url: str = (
            getattr(self, "_reauth_standalone_auth_url", "")
            or oauth.get_authorization_url()
        )

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
                    creds = await self.hass.async_add_executor_job(
                        oauth.exchange_code, code
                    )
                except ValueError:
                    errors["redirect_url_or_code"] = "invalid_redirect_url"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Code exchange failed during reauth")
                    errors["redirect_url_or_code"] = "code_exchange_failed"
                else:
                    reauth_entry = self._get_reauth_entry()
                    new_data = {
                        **reauth_entry.data,
                        CONF_OAUTH_CLIENT_ID: self._reauth_standalone_client_id,
                        CONF_OAUTH_CLIENT_SECRET: self._reauth_standalone_client_secret,
                        CONF_OAUTH_REFRESH_TOKEN: creds.refresh_token,
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
