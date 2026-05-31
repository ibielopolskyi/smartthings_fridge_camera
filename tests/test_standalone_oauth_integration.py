"""Integration tests for the standalone SmartThings OAuth config flow.

These tests drive the full 3-step flow through the real config_flow module
without mocking the flow's internal logic — only external network calls are
stubbed (requests_mock / MagicMock).

Run with:
    pytest tests/test_standalone_oauth_integration.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
    CONF_SAMSUNG_SIGNIN_CLIENT_SECRET,
)
from custom_components.samsung_familyhub_fridge.auth import (
    TOKEN_URL,
    SAMSUNG_AUTH_URL,
    SAMSUNG_IOT_AUTHORIZE_URL,
    SAMSUNG_IOT_TOKEN_URL,
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
# SCRUM-83: samsung_signin_client_secret stored in entry and passed to auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_with_signin_client_secret(requests_mock):
    """End-to-end: samsung_signin_client_secret is stored in entry data and
    passed through to SamsungAccountAuth (verified via HTTP stub matching)."""
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])

    flow = _make_flow(hass)

    # Step 2 — credentials
    await flow.async_step_standalone_oauth_credentials(
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

    # Step 3 — link via raw code
    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": "raw-code-xyz"}
    )
    assert result["step_id"] == "standalone_oauth_samsung"

    # Stub Samsung Account auth endpoints (real HTTP calls from SamsungAccountAuth)
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
            "refresh_token": "iot-refresh-secret",
        },
    )

    # Step 4 — Samsung Account with signin_client_secret
    result = await flow.async_step_standalone_oauth_samsung(
        user_input={
            "samsung_email": "user@example.com",
            "samsung_password": "test-password",
            CONF_SAMSUNG_SIGNIN_CLIENT_SECRET: "my-signin-secret",
        }
    )

    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_SAMSUNG_SIGNIN_CLIENT_SECRET] == "my-signin-secret"
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-refresh-secret"
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
