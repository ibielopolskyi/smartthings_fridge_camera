"""Tests for keeping the Samsung picture login alive.

- Daily renewal is scheduled when a Samsung login is stored.
- Renewal falls back to the stored userauth token if the refresh token is
  refused.
- Renewed refresh tokens are saved on HA's event loop, not from the
  executor thread.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _ConfigEntry, _HomeAssistant, _ServiceRegistry

from custom_components.samsung_familyhub_fridge.api import FamilyHub
from custom_components.samsung_familyhub_fridge.auth import (
    AuthError,
    renew_samsung_iot_token,
)
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
    CONF_SAMSUNG_LOGIN_ID,
    CONF_SAMSUNG_PHYSICAL_ADDRESS,
    CONF_SAMSUNG_USERAUTH_TOKEN,
)

AUTH = "https://us-auth2.samsungosp.com"
TOKEN = f"{AUTH}/auth/oauth2/token"
AUTHORIZE = f"{AUTH}/auth/oauth2/v2/authorize"


# ---------------------------------------------------------------------------
# renew_samsung_iot_token
# ---------------------------------------------------------------------------

def test_renew_uses_refresh_token_when_it_works():
    with rm.Mocker() as m:
        m.post(TOKEN, json={"access_token": "A2", "refresh_token": "R2"})
        creds = renew_samsung_iot_token("R1", AUTH, userauth_token="UAT")
        assert len(m.request_history) == 1
    assert (creds.access_token, creds.refresh_token) == ("A2", "R2")


def test_renew_falls_back_to_userauth_token():
    with rm.Mocker() as m:
        m.post(TOKEN, [
            {"status_code": 400, "json": {"error": "invalid_grant"}},
            {"json": {"access_token": "A3", "refresh_token": "R3"}},
        ])
        m.get(AUTHORIZE, json={"code": "IOT-CODE"})
        creds = renew_samsung_iot_token(
            "R1", AUTH, userauth_token="UAT", login_id="me@example.com",
            physical_address_text="dev-1",
        )
        authorize = [r for r in m.request_history if "authorize" in r.url][0]
        assert authorize.qs["userauth_token"] == ["uat"]  # requests_mock lowercases
        assert authorize.qs["physical_address_text"] == ["dev-1"]
    assert creds.refresh_token == "R3"


def test_renew_without_backup_raises():
    with rm.Mocker() as m:
        m.post(TOKEN, status_code=400, json={"error": "invalid_grant"})
        with pytest.raises(Exception):
            renew_samsung_iot_token("R1", AUTH)


def test_renew_with_both_failing_reports_both():
    with rm.Mocker() as m:
        m.post(TOKEN, status_code=400, json={"error": "invalid_grant"})
        m.get(AUTHORIZE, status_code=401, text="expired")
        with pytest.raises(AuthError, match="backup token"):
            renew_samsung_iot_token("R1", AUTH, userauth_token="UAT")


# ---------------------------------------------------------------------------
# FamilyHub.renew_samsung_iot + thread-safe save
# ---------------------------------------------------------------------------

def test_renew_saves_new_refresh_token_via_event_loop():
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.loop = MagicMock()  # HA's loop, not running in this (executor) thread
    entry = _ConfigEntry(data={CONF_SAMSUNG_IOT_REFRESH_TOKEN: "R1"})
    hub = FamilyHub(hass, token="st", device_id="fridge-1")
    hub.configure_samsung_iot("R1", AUTH, entry)
    hub.images_unavailable_reason = "no_samsung_account"

    with rm.Mocker() as m:
        m.post(TOKEN, json={"access_token": "A2", "refresh_token": "R2"})
        hub.renew_samsung_iot()

    assert hub._samsung_iot_headers["Authorization"] == "Bearer A2"
    assert hub.images_unavailable_reason is None
    # Not saved directly from this thread...
    hass.config_entries.async_update_entry.assert_not_called()
    # ...but handed to the event loop, which then saves it.
    hass.loop.call_soon_threadsafe.assert_called_once()
    hass.loop.call_soon_threadsafe.call_args[0][0]()
    _, kwargs = hass.config_entries.async_update_entry.call_args
    assert kwargs["data"][CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "R2"


# ---------------------------------------------------------------------------
# async_setup_entry: backup wiring + daily renewal
# ---------------------------------------------------------------------------

def _make_hass():
    hass = _HomeAssistant()
    hass.data = {}
    hass.services = _ServiceRegistry()
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    return hass


@pytest.mark.asyncio
async def test_setup_schedules_daily_renewal_and_uses_backup():
    from custom_components.samsung_familyhub_fridge import async_setup_entry
    from custom_components.samsung_familyhub_fridge.auth import TOKEN_URL

    hass = _make_hass()
    entry = _ConfigEntry(entry_id="e1", data={
        CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
        CONF_OAUTH_CLIENT_ID: "cid",
        CONF_OAUTH_CLIENT_SECRET: "csec",
        CONF_OAUTH_REFRESH_TOKEN: "st-rt",
        CONF_DEVICE_ID: "fridge-1",
        CONF_SAMSUNG_IOT_REFRESH_TOKEN: "R1",
        CONF_SAMSUNG_IOT_AUTH_SERVER: AUTH,
        CONF_SAMSUNG_USERAUTH_TOKEN: "UAT",
        CONF_SAMSUNG_LOGIN_ID: "me@example.com",
        CONF_SAMSUNG_PHYSICAL_ADDRESS: "dev-1",
    })
    tracker = MagicMock(return_value=lambda: None)
    with rm.Mocker() as m, patch(
        "custom_components.samsung_familyhub_fridge.async_track_time_interval",
        tracker,
    ):
        m.post(TOKEN_URL, json={"access_token": "st-at", "refresh_token": "st-rt"})
        # Samsung refuses the refresh token at start-up → backup is used.
        m.post(TOKEN, [
            {"status_code": 400, "json": {"error": "invalid_grant"}},
            {"json": {"access_token": "A3", "refresh_token": "R3"}},
            {"json": {"access_token": "A4", "refresh_token": "R4"}},
        ])
        m.get(AUTHORIZE, json={"code": "IOT-CODE"})

        assert await async_setup_entry(hass, entry) is True
        hub = hass.data["samsung_familyhub_fridge"]["hub"]
        assert hub._samsung_iot_headers["Authorization"] == "Bearer A3"

        tracker.assert_called_once()
        _, callback, interval = tracker.call_args[0]
        assert interval == timedelta(hours=24)

        # The daily callback renews again (now with the refresh token).
        await callback()
        assert hub._samsung_iot_headers["Authorization"] == "Bearer A4"


@pytest.mark.asyncio
async def test_setup_without_samsung_login_schedules_nothing():
    from custom_components.samsung_familyhub_fridge import async_setup_entry
    from custom_components.samsung_familyhub_fridge.auth import TOKEN_URL

    hass = _make_hass()
    entry = _ConfigEntry(entry_id="e2", data={
        CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
        CONF_OAUTH_CLIENT_ID: "cid",
        CONF_OAUTH_CLIENT_SECRET: "csec",
        CONF_OAUTH_REFRESH_TOKEN: "st-rt",
        CONF_DEVICE_ID: "fridge-1",
    })
    tracker = MagicMock(return_value=lambda: None)
    with rm.Mocker() as m, patch(
        "custom_components.samsung_familyhub_fridge.async_track_time_interval",
        tracker,
    ):
        m.post(TOKEN_URL, json={"access_token": "st-at", "refresh_token": "st-rt"})
        assert await async_setup_entry(hass, entry) is True
    tracker.assert_not_called()


# ---------------------------------------------------------------------------
# Config flow stores the backup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_sign_in_stores_backup_token():
    from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow

    flow = ConfigFlow()
    flow.hass = _HomeAssistant()
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "data": data}
    flow._pending_entry = ("t", {CONF_AUTH_MODE: "oauth"})
    flow._browser_url = "https://x"
    login = MagicMock()
    login.userauth_token = "UAT"
    login.username = "me@example.com"
    login.device_id = "dev-1"
    login.finish.return_value = MagicMock(refresh_token="R1", auth_server_url=AUTH)
    flow._browser_login = login

    result = await flow.async_step_samsung_browser({"redirect_url": "ms-app://x?code=1"})
    data = result["data"]
    assert data[CONF_SAMSUNG_USERAUTH_TOKEN] == "UAT"
    assert data[CONF_SAMSUNG_LOGIN_ID] == "me@example.com"
    assert data[CONF_SAMSUNG_PHYSICAL_ADDRESS] == "dev-1"
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "R1"


@pytest.mark.asyncio
async def test_password_login_stores_backup_token():
    from custom_components.samsung_familyhub_fridge.config_flow import _samsung_iot_login

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth"
    ) as MockAuth:
        inst = MockAuth.return_value
        inst.login_iot.return_value = MagicMock(refresh_token="R1", auth_server_url=AUTH)
        inst.userauth_token = "UAT"
        inst._device_id = "dev-1"
        data = await _samsung_iot_login(
            _HomeAssistant(),
            {"samsung_email": "me@example.com", "samsung_password": "pw"},
        )
    assert data[CONF_SAMSUNG_USERAUTH_TOKEN] == "UAT"
    assert data[CONF_SAMSUNG_LOGIN_ID] == "me@example.com"
