"""Integration tests for the standalone SmartThings OAuth config flow and runtime.

Part 1 (config flow): drives the full 3-step flow through the real config_flow
module without mocking the flow's internal logic — only external network calls
are stubbed (requests_mock / MagicMock).

Part 2 (runtime): real-API tests that exercise token refresh against live
SmartThings endpoints. These auto-skip unless standalone OAuth credentials are
passed via CLI flags:
    --standalone-oauth-client-id CID
    --standalone-oauth-client-secret SECRET
    --standalone-oauth-refresh-token REFRESH

Run with:
    pytest tests/test_standalone_oauth_integration.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests_mock as rm

import time

from tests.conftest import _HomeAssistant, _ConfigEntry

from custom_components.samsung_familyhub_fridge.api import FamilyHub
from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)
from custom_components.samsung_familyhub_fridge.auth import (
    TOKEN_URL,
    SAMSUNG_AUTH_URL,
    SAMSUNG_IOT_AUTHORIZE_URL,
    SAMSUNG_IOT_TOKEN_URL,
    SmartThingsOAuth,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(hass) -> ConfigFlow:
    flow = ConfigFlow()
    flow.hass = hass
    return flow


# ---------------------------------------------------------------------------
# Full happy-path: credentials → link (raw code) → samsung (skipped)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_raw_code_skip_samsung(requests_mock):
    """End-to-end: enter credentials, exchange raw code, skip Samsung login."""
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])

    flow = _make_flow(hass)

    # Step 1 — menu
    result = await flow.async_step_user()
    assert "standalone_oauth" in result["menu_options"]

    # Step 2 — credentials
    result = await flow.async_step_standalone_oauth_credentials(
        user_input={
            CONF_OAUTH_CLIENT_ID: "integ-client-id",
            CONF_OAUTH_CLIENT_SECRET: "integ-client-secret",
        }
    )
    assert result["step_id"] == "standalone_oauth_link", f"Expected link step, got: {result}"

    # Stub the token exchange endpoint
    requests_mock.post(
        TOKEN_URL,
        json={
            "access_token": "integ-access-token",
            "refresh_token": "integ-refresh-token",
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )

    # Step 3 — link (raw auth code)
    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": "raw-code-xyz"}
    )
    assert result["step_id"] == "standalone_oauth_samsung", f"Expected samsung step, got: {result}"
    assert flow._standalone_refresh_token == "integ-refresh-token"

    # Step 4 — samsung (skip by sending empty credentials)
    result = await flow.async_step_standalone_oauth_samsung(
        user_input={"samsung_email": "", "samsung_password": ""}
    )
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_OAUTH_CLIENT_ID] == "integ-client-id"
    assert data[CONF_OAUTH_CLIENT_SECRET] == "integ-client-secret"
    assert data[CONF_OAUTH_REFRESH_TOKEN] == "integ-refresh-token"
    assert CONF_SAMSUNG_IOT_REFRESH_TOKEN not in data


# ---------------------------------------------------------------------------
# Full happy-path: credentials → link (redirect URL) → samsung login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_redirect_url_with_samsung(requests_mock):
    """End-to-end: enter credentials, exchange via redirect URL, Samsung login."""
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])

    flow = _make_flow(hass)

    # Step 2 — credentials
    result = await flow.async_step_standalone_oauth_credentials(
        user_input={
            CONF_OAUTH_CLIENT_ID: "integ-client-id",
            CONF_OAUTH_CLIENT_SECRET: "integ-client-secret",
        }
    )

    # Stub token exchange
    requests_mock.post(
        TOKEN_URL,
        json={
            "access_token": "integ-access-token",
            "refresh_token": "integ-refresh-token",
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )

    # Step 3 — link via redirect URL
    redirect_url = "https://httpbin.org/get?code=redirect-code-abc&state=xyz"
    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": redirect_url}
    )
    assert result["step_id"] == "standalone_oauth_samsung"

    # Stub Samsung Account login → IoT token flow
    requests_mock.post(
        SAMSUNG_AUTH_URL,
        json={"userauth_token": "samsung-userauth-tok"},
    )
    requests_mock.get(
        SAMSUNG_IOT_AUTHORIZE_URL,
        json={"code": "iot-auth-code"},
        headers={"Content-Type": "application/json"},
        status_code=200,
    )
    requests_mock.post(
        SAMSUNG_IOT_TOKEN_URL,
        json={
            "access_token": "iot-access-token",
            "refresh_token": "iot-refresh-token",
        },
    )

    # Step 4 — Samsung Account credentials provided
    result = await flow.async_step_standalone_oauth_samsung(
        user_input={
            "samsung_email": "user@example.com",
            "samsung_password": "test-password",
        }
    )

    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_OAUTH_REFRESH_TOKEN] == "integ-refresh-token"
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-refresh-token"
    assert data[CONF_SAMSUNG_IOT_AUTH_SERVER] == "https://us-auth2.samsungosp.com"


# ---------------------------------------------------------------------------
# Error path: invalid redirect URL shows a clear error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_redirect_url_shows_error():
    """Link step: URL with no 'code' param shows invalid_redirect_url error."""
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    flow._standalone_auth_url = "https://api.smartthings.com/oauth/authorize?test=1"
    flow._standalone_oauth = MagicMock()
    flow._standalone_oauth.get_authorization_url.return_value = "https://api.smartthings.com/oauth/authorize"

    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": "https://httpbin.org/get?no_code_here=1"}
    )
    assert result["step_id"] == "standalone_oauth_link"
    assert result["errors"]["redirect_url_or_code"] == "invalid_redirect_url"


# ===========================================================================
# Part 2 — Runtime integration tests (real SmartThings API calls)
#
# These tests auto-skip when standalone OAuth credentials are absent.
# Pass via CLI:
#   --standalone-oauth-client-id CID
#   --standalone-oauth-client-secret SECRET
#   --standalone-oauth-refresh-token REFRESH
# ===========================================================================


# ---------------------------------------------------------------------------
# Runtime test 1: SmartThingsOAuth.refresh() returns a valid token pair
# ---------------------------------------------------------------------------

def test_real_oauth_refresh_returns_tokens(standalone_oauth_credentials):
    """Real refresh call returns access_token and a (possibly rotated) refresh_token."""
    creds = standalone_oauth_credentials
    oauth = SmartThingsOAuth(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
    result = oauth.refresh(creds["refresh_token"])

    assert result.access_token, "Expected a non-empty access_token"
    assert result.refresh_token, "Expected a non-empty refresh_token"
    assert result.expires_in > 0, "Expected a positive expires_in"


# ---------------------------------------------------------------------------
# Runtime test 2: FamilyHub.attach_standalone_oauth + ensure_fresh_token
#                 refreshes the access token when near expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_ensure_fresh_token_refreshes(standalone_oauth_credentials):
    """attach_standalone_oauth + async_ensure_fresh_token refreshes near-expiry token."""
    creds = standalone_oauth_credentials
    hass = _HomeAssistant()
    entry = _ConfigEntry(
        data={
            CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
            CONF_OAUTH_CLIENT_ID: creds["client_id"],
            CONF_OAUTH_CLIENT_SECRET: creds["client_secret"],
            CONF_OAUTH_REFRESH_TOKEN: creds["refresh_token"],
        }
    )
    oauth = SmartThingsOAuth(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
    hub = FamilyHub(hass, token="placeholder", device_id=None)
    # Set expires_at to now − 1 so the token is already "expired" → must refresh
    hub.attach_standalone_oauth(
        oauth,
        expires_at=time.time() - 1,
        refresh_token=creds["refresh_token"],
        config_entry=entry,
    )

    await hub.async_ensure_fresh_token()

    assert hub.token != "placeholder", "Token should have been updated after refresh"
    assert hub._stored_refresh_token, "Refresh token should be set after refresh"
    assert entry.data[CONF_OAUTH_REFRESH_TOKEN] == hub._stored_refresh_token


# ---------------------------------------------------------------------------
# Runtime test 3: FamilyHub skips refresh when token is not near expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_ensure_fresh_token_skips_when_not_expiring(standalone_oauth_credentials):
    """async_ensure_fresh_token does NOT call the network when token is fresh."""
    creds = standalone_oauth_credentials
    hass = _HomeAssistant()
    oauth = SmartThingsOAuth(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
    hub = FamilyHub(hass, token="sentinel-token", device_id=None)
    # Expiry is 1 hour away — well outside the 5-minute refresh window
    hub.attach_standalone_oauth(
        oauth,
        expires_at=time.time() + 3600,
        refresh_token=creds["refresh_token"],
    )

    await hub.async_ensure_fresh_token()

    # Token must be unchanged — no network call was made
    assert hub.token == "sentinel-token"
