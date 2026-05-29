"""Unit tests for standalone SmartThings OAuth runtime: token refresh & reauth.

Covers:
  1. FamilyHub initial state for standalone OAuth attributes
  2. attach_standalone_oauth populates all attributes
  3. async_ensure_fresh_token is no-op when no standalone oauth attached
  4. async_ensure_fresh_token skips refresh when token is not near expiry
  5. async_ensure_fresh_token refreshes when within 5-minute window
  6. After refresh, new refresh token is persisted to config entry
  7. async_ensure_fresh_token raises ConfigEntryAuthFailed on HTTP 401

[Ecthelion Gate-Prover]
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

# conftest installs HA stubs before any component imports
from tests.conftest import _HomeAssistant, _ConfigEntry, _ConfigEntryAuthFailed

from custom_components.samsung_familyhub_fridge.api import FamilyHub
from custom_components.samsung_familyhub_fridge.const import (
    CONF_OAUTH_REFRESH_TOKEN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    hass = _HomeAssistant()
    return hass


def _make_hub(hass=None, token="test-access-token", device_id="device-123"):
    if hass is None:
        hass = _make_hass()
    return FamilyHub(hass, token=token, device_id=device_id)


def _make_oauth_creds(access="new-access-token", refresh="new-refresh-token", expires_in=86400):
    creds = MagicMock()
    creds.access_token = access
    creds.refresh_token = refresh
    creds.expires_in = expires_in
    return creds


def _make_http_error(status_code: int):
    """Create a requests.HTTPError with the given status code."""
    resp = requests.models.Response()
    resp.status_code = status_code
    err = requests.exceptions.HTTPError(response=resp)
    return err


# ---------------------------------------------------------------------------
# Test 1: Initial state
# ---------------------------------------------------------------------------

def test_initial_standalone_oauth_attributes():
    """FamilyHub starts with standalone OAuth disabled."""
    hub = _make_hub()
    assert hub._standalone_oauth is None
    assert hub._token_expires_at == 0.0
    assert hub._stored_refresh_token == ""
    assert hub._config_entry is None


# ---------------------------------------------------------------------------
# Test 2: attach_standalone_oauth populates attributes
# ---------------------------------------------------------------------------

def test_attach_standalone_oauth_sets_attributes():
    """attach_standalone_oauth stores oauth instance, expiry, refresh token, entry."""
    hub = _make_hub()
    oauth_mock = MagicMock()
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: "old-refresh"})
    expires_at = time.time() + 3600

    hub.attach_standalone_oauth(
        oauth_mock,
        expires_at,
        refresh_token="stored-refresh",
        config_entry=entry,
    )

    assert hub._standalone_oauth is oauth_mock
    assert hub._token_expires_at == expires_at
    assert hub._stored_refresh_token == "stored-refresh"
    assert hub._config_entry is entry


# ---------------------------------------------------------------------------
# Test 3: No-op when standalone oauth not attached
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_noop_without_standalone_oauth():
    """async_ensure_fresh_token is a no-op when no standalone oauth is set."""
    hub = _make_hub()
    # Should not raise or modify token
    await hub.async_ensure_fresh_token()
    assert hub.token == "test-access-token"


# ---------------------------------------------------------------------------
# Test 4: Skips refresh when token is not near expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_skips_when_not_near_expiry():
    """Token is not refreshed when more than 5 minutes remain."""
    hub = _make_hub()
    oauth_mock = MagicMock()
    # Expiry is 10 minutes in the future — beyond the 5-minute refresh window
    hub.attach_standalone_oauth(
        oauth_mock,
        expires_at=time.time() + 600,
        refresh_token="current-refresh",
    )

    await hub.async_ensure_fresh_token()

    oauth_mock.refresh.assert_not_called()
    assert hub.token == "test-access-token"


# ---------------------------------------------------------------------------
# Test 5: Refreshes when within 5-minute window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_refreshes_when_near_expiry():
    """Token IS refreshed when within 5 minutes of expiry."""
    hub = _make_hub()
    oauth_mock = MagicMock()
    oauth_mock.refresh.return_value = _make_oauth_creds(
        access="refreshed-token", refresh="new-refresh", expires_in=86400
    )
    # Expiry is only 2 minutes away — inside the 5-minute window
    hub.attach_standalone_oauth(
        oauth_mock,
        expires_at=time.time() + 120,
        refresh_token="current-refresh",
    )

    await hub.async_ensure_fresh_token()

    oauth_mock.refresh.assert_called_once_with("current-refresh")
    assert hub.token == "refreshed-token"
    assert hub._stored_refresh_token == "new-refresh"


# ---------------------------------------------------------------------------
# Test 6: New refresh token persisted to config entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_persists_new_refresh_token():
    """After refresh, the new refresh token is written to the config entry."""
    hass = _make_hass()
    hub = _make_hub(hass=hass)

    entry = _ConfigEntry(
        entry_id="entry-1",
        data={
            CONF_OAUTH_REFRESH_TOKEN: "old-refresh",
            "auth_mode": "standalone_oauth",
        },
    )
    oauth_mock = MagicMock()
    oauth_mock.refresh.return_value = _make_oauth_creds(
        access="new-access", refresh="rotated-refresh", expires_in=86400
    )
    hub.attach_standalone_oauth(
        oauth_mock,
        expires_at=time.time() + 60,  # expires in 1 minute — inside window
        refresh_token="old-refresh",
        config_entry=entry,
    )

    await hub.async_ensure_fresh_token()

    # Config entry data should be updated with the new refresh token
    assert entry.data[CONF_OAUTH_REFRESH_TOKEN] == "rotated-refresh"
    # Other keys preserved
    assert entry.data["auth_mode"] == "standalone_oauth"
    # Hub token updated
    assert hub.token == "new-access"


# ---------------------------------------------------------------------------
# Test 7: HTTP 401 raises ConfigEntryAuthFailed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_fresh_token_raises_config_entry_auth_failed_on_401():
    """A 401 from the refresh endpoint raises ConfigEntryAuthFailed."""
    hub = _make_hub()
    oauth_mock = MagicMock()
    oauth_mock.refresh.side_effect = _make_http_error(401)

    hub.attach_standalone_oauth(
        oauth_mock,
        expires_at=time.time() + 60,  # within window
        refresh_token="expired-refresh",
    )

    with pytest.raises(_ConfigEntryAuthFailed):
        await hub.async_ensure_fresh_token()
