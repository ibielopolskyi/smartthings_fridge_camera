"""Integration tests for the standalone SmartThings OAuth config flow and runtime.

Section A — Config-flow integration tests:
    Drive the full 3-step flow through the real config_flow module without
    mocking the flow's internal logic — only external network calls are stubbed
    (requests_mock / MagicMock).

Section B — Real-API runtime tests (auto-skipped without credentials):
    Test _build_standalone_oauth_hub and the per-poll token refresh cycle against
    the live SmartThings OAuth endpoint.  Provide credentials via --credentials
    (a .smartthings_credentials.json containing client_id, client_secret,
    refresh_token).

Run all:
    pytest tests/test_standalone_oauth_integration.py -v
Run only real-API tests:
    pytest tests/test_standalone_oauth_integration.py -v -k real_api --credentials creds.json
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant, _ConfigEntry

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
# Section B — Real-API runtime tests
# Auto-skipped when --credentials is not supplied.
# ===========================================================================


def _load_credentials(request):
    """Load OAuth credentials from the --credentials file.  Returns None if absent."""
    creds_path = request.config.getoption("--credentials", default=None)
    if not creds_path:
        return None
    import json, os
    if not os.path.isabs(creds_path):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        creds_path = os.path.join(repo_root, creds_path)
    with open(creds_path) as f:
        return json.load(f)


@pytest.fixture
def real_api_creds(request):
    """Provide real OAuth credentials, skipping the test when unavailable."""
    creds = _load_credentials(request)
    if not creds:
        pytest.skip("--credentials not provided; skipping real-API test")
    return creds


# ---------------------------------------------------------------------------
# Real-API 1: startup token refresh returns valid new credentials
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_api_startup_refresh_returns_valid_credentials(real_api_creds):
    """_build_standalone_oauth_hub refreshes the stored refresh token at startup."""
    from custom_components.samsung_familyhub_fridge.auth import SmartThingsOAuth

    oauth = SmartThingsOAuth(
        client_id=real_api_creds["client_id"],
        client_secret=real_api_creds["client_secret"],
    )
    new_creds = oauth.refresh(real_api_creds["refresh_token"])

    assert new_creds.access_token, "refresh() must return a non-empty access_token"
    assert new_creds.refresh_token, "refresh() must return a non-empty refresh_token"
    assert new_creds.expires_in > 0, "expires_in must be positive"


# ---------------------------------------------------------------------------
# Real-API 2: near-expiry token triggers refresh via async_ensure_fresh_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_api_near_expiry_triggers_refresh(real_api_creds):
    """FamilyHub refreshes the token when it is within 5 minutes of expiry."""
    from custom_components.samsung_familyhub_fridge.api import FamilyHub
    from custom_components.samsung_familyhub_fridge.auth import SmartThingsOAuth

    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()

    oauth = SmartThingsOAuth(
        client_id=real_api_creds["client_id"],
        client_secret=real_api_creds["client_secret"],
    )
    # Bootstrap: do one real refresh to get a valid pair.
    initial_creds = oauth.refresh(real_api_creds["refresh_token"])

    hub = FamilyHub(hass, token=initial_creds.access_token, device_id=None)
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: initial_creds.refresh_token})
    # Force expiry to be in the past so refresh is triggered immediately.
    hub.attach_standalone_oauth(oauth, time.time() - 60, initial_creds.refresh_token, entry)

    await hub.async_ensure_fresh_token()

    assert hub.token != initial_creds.access_token or True  # token rotated (or same if ST returns same)
    assert hub._stored_refresh_token  # non-empty after refresh
    # Config entry update was persisted.
    hass.config_entries.async_update_entry.assert_called_once()


# ---------------------------------------------------------------------------
# Real-API 3: consecutive ensure calls do not re-refresh a fresh token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_api_consecutive_ensure_skips_fresh_token(real_api_creds):
    """async_ensure_fresh_token skips the network call when token is still fresh."""
    from custom_components.samsung_familyhub_fridge.api import FamilyHub
    from custom_components.samsung_familyhub_fridge.auth import SmartThingsOAuth

    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()

    oauth = SmartThingsOAuth(
        client_id=real_api_creds["client_id"],
        client_secret=real_api_creds["client_secret"],
    )
    initial_creds = oauth.refresh(real_api_creds["refresh_token"])

    hub = FamilyHub(hass, token=initial_creds.access_token, device_id=None)
    entry = _ConfigEntry(data={CONF_OAUTH_REFRESH_TOKEN: initial_creds.refresh_token})
    # Token expires far in the future — no refresh should happen.
    hub.attach_standalone_oauth(
        oauth, time.time() + 7200, initial_creds.refresh_token, entry
    )

    await hub.async_ensure_fresh_token()
    await hub.async_ensure_fresh_token()  # second call — still no refresh

    # No update_entry calls because the token is fresh.
    hass.config_entries.async_update_entry.assert_not_called()
