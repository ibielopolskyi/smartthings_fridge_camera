"""Unit tests for standalone SmartThings OAuth runtime: token refresh & setup.

Covers FamilyHub.attach_standalone_oauth(), async_ensure_fresh_token() in
standalone OAuth mode, and _build_standalone_oauth_hub() wiring in __init__.py.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _HomeAssistant, _ConfigEntry

from custom_components.samsung_familyhub_fridge.api import FamilyHub
from custom_components.samsung_familyhub_fridge.auth import AuthError
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


def _make_hub(hass=None, token="initial-token", device_id="dev-123"):
    if hass is None:
        hass = _make_hass()
    return FamilyHub(hass, token=token, device_id=device_id)


def _fake_creds(access="new-access", refresh="new-refresh", expires_in=3600):
    creds = MagicMock()
    creds.access_token = access
    creds.refresh_token = refresh
    creds.expires_in = expires_in
    return creds


# ---------------------------------------------------------------------------
# 1. FamilyHub initialises standalone OAuth attributes to safe defaults
# ---------------------------------------------------------------------------

def test_family_hub_init_standalone_oauth_defaults():
    hass = _make_hass()
    hub = FamilyHub(hass, token="tok", device_id="dev")
    assert hub._standalone_oauth is None
    assert hub._token_expires_at == 0.0
    assert hub._stored_refresh_token is None
    assert hub._config_entry is None


# ---------------------------------------------------------------------------
# 2. attach_standalone_oauth stores all four fields correctly
# ---------------------------------------------------------------------------

def test_attach_standalone_oauth_sets_all_attributes():
    hass = _make_hass()
    hub = _make_hub(hass)
    oauth_mock = MagicMock()
    entry = _ConfigEntry(data={})
    expires = time.time() + 3600
    hub.attach_standalone_oauth(oauth_mock, expires, "stored-rt", entry)

    assert hub._standalone_oauth is oauth_mock
    assert hub._token_expires_at == expires
    assert hub._stored_refresh_token == "stored-rt"
    assert hub._config_entry is entry


# ---------------------------------------------------------------------------
# 3. async_ensure_fresh_token is a no-op in PAT mode (no oauth session)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_noop_in_pat_mode():
    hass = _make_hass()
    hub = _make_hub(hass, token="pat-token")
    # No oauth session, no standalone oauth — should be a pure no-op.
    original_token = hub.token
    await hub.async_ensure_fresh_token()
    assert hub.token == original_token
    hass.config_entries.async_update_entry.assert_not_called()


# ---------------------------------------------------------------------------
# 4. async_ensure_fresh_token skips refresh when token is not near expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_skips_when_not_expiring():
    hass = _make_hass()
    hub = _make_hub(hass, token="tok")
    oauth_mock = MagicMock()
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: "old-rt"})
    # Token expires far in the future — no refresh should happen.
    hub.attach_standalone_oauth(oauth_mock, time.time() + 7200, "old-rt", entry)

    await hub.async_ensure_fresh_token()

    oauth_mock.refresh.assert_not_called()
    hass.config_entries.async_update_entry.assert_not_called()


# ---------------------------------------------------------------------------
# 5. async_ensure_fresh_token refreshes when within 5-minute window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_refreshes_when_near_expiry():
    hass = _make_hass()
    hub = _make_hub(hass, token="old-tok")
    new_creds = _fake_creds(access="refreshed-tok", refresh="refreshed-rt")
    oauth_mock = MagicMock()
    oauth_mock.refresh.return_value = new_creds
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: "old-rt"})
    # Token already expired (expires_at in the past) → within 5-min window.
    hub.attach_standalone_oauth(oauth_mock, time.time() - 60, "old-rt", entry)

    await hub.async_ensure_fresh_token()

    oauth_mock.refresh.assert_called_once_with("old-rt")
    assert hub.token == "refreshed-tok"
    assert hub._stored_refresh_token == "refreshed-rt"


# ---------------------------------------------------------------------------
# 6. async_ensure_fresh_token persists new tokens to config entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_persists_new_tokens():
    hass = _make_hass()
    hub = _make_hub(hass, token="old-tok")
    new_creds = _fake_creds(access="new-access", refresh="new-rt", expires_in=3600)
    oauth_mock = MagicMock()
    oauth_mock.refresh.return_value = new_creds
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: "old-rt", CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH})
    hub.attach_standalone_oauth(oauth_mock, time.time() - 1, "old-rt", entry)

    await hub.async_ensure_fresh_token()

    hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = hass.config_entries.async_update_entry.call_args
    persisted_data = call_kwargs[1]["data"] if call_kwargs[1] else call_kwargs[0][1]
    assert persisted_data[CONF_OAUTH_REFRESH_TOKEN] == "new-rt"


# ---------------------------------------------------------------------------
# 7. async_ensure_fresh_token raises ConfigEntryAuthFailed on AuthError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_raises_config_entry_auth_failed_on_auth_error():
    from homeassistant.exceptions import ConfigEntryAuthFailed

    hass = _make_hass()
    hub = _make_hub(hass, token="tok")
    oauth_mock = MagicMock()
    oauth_mock.refresh.side_effect = AuthError("Refresh token rejected")
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: "bad-rt"})
    hub.attach_standalone_oauth(oauth_mock, time.time() - 60, "bad-rt", entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await hub.async_ensure_fresh_token()
