"""Tests for standalone OAuth auth_mode wiring in async_setup_entry and config_flow.

Acceptance criteria (SCRUM-80):
- Integration test: config entry with auth_mode=standalone_oauth; mock SmartThings
  token refresh; assert hub.token is the refreshed access token (not None).
- Integration test: same + samsung_iot_refresh_token; mock Samsung IoT refresh;
  assert hub._samsung_iot_headers is set.
- Unit test: async_step_reauth with standalone OAuth entry routes to
  async_step_reauth_standalone_oauth, not async_step_reauth_confirm.
- Run: python3 -m pytest tests/ -k 'standalone_oauth' -v → all pass.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant, _ConfigEntry

from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
    DOMAIN,
)
from custom_components.samsung_familyhub_fridge.auth import TOKEN_URL, SAMSUNG_IOT_TOKEN_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_standalone_entry(extra_data=None):
    data = {
        CONF_AUTH_MODE: AUTH_MODE_STANDALONE_OAUTH,
        CONF_OAUTH_CLIENT_ID: "test-client-id",
        CONF_OAUTH_CLIENT_SECRET: "test-client-secret",
        CONF_OAUTH_REFRESH_TOKEN: "old-refresh-token",
        CONF_DEVICE_ID: "fridge-device-id",
    }
    if extra_data:
        data.update(extra_data)
    return _ConfigEntry(entry_id="test-entry", data=data)


def _make_hass(entry):
    hass = _HomeAssistant()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=None)
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    return hass


# ---------------------------------------------------------------------------
# Integration test 1: hub.token is the refreshed access token (not None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_entry_standalone_oauth_sets_hub_token(requests_mock):
    """async_setup_entry with standalone_oauth calls SmartThings token refresh and
    sets hub.token to the new access token."""
    # Stub SmartThings token refresh endpoint
    requests_mock.post(
        TOKEN_URL,
        json={
            "access_token": "fresh-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )

    entry = _make_standalone_entry()
    hass = _make_hass(entry)

    from custom_components.samsung_familyhub_fridge import async_setup_entry

    result = await async_setup_entry(hass, entry)

    assert result is True
    hub = hass.data[DOMAIN]["hub"]
    assert hub.token == "fresh-access-token", (
        f"Expected hub.token='fresh-access-token', got {hub.token!r}"
    )
    assert hub.token is not None


# ---------------------------------------------------------------------------
# Integration test 2: Samsung IoT token is attached when refresh token present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_entry_standalone_oauth_attaches_samsung_iot_token(requests_mock):
    """async_setup_entry with standalone_oauth and samsung_iot_refresh_token
    calls the Samsung IoT refresh endpoint and sets hub._samsung_iot_headers."""
    # Stub SmartThings token refresh
    requests_mock.post(
        TOKEN_URL,
        json={
            "access_token": "fresh-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )
    # Stub Samsung IoT refresh endpoint
    requests_mock.post(
        "https://us-auth2.samsungosp.com/auth/oauth2/token",
        json={
            "access_token": "iot-fresh-access-token",
            "refresh_token": "iot-new-refresh-token",
        },
    )

    entry = _make_standalone_entry(extra_data={
        CONF_SAMSUNG_IOT_REFRESH_TOKEN: "iot-old-refresh-token",
        CONF_SAMSUNG_IOT_AUTH_SERVER: "https://us-auth2.samsungosp.com",
    })
    hass = _make_hass(entry)

    from custom_components.samsung_familyhub_fridge import async_setup_entry

    result = await async_setup_entry(hass, entry)

    assert result is True
    hub = hass.data[DOMAIN]["hub"]
    assert hub.token == "fresh-access-token"
    # Samsung IoT headers should be present and contain the fresh token
    assert hub._samsung_iot_headers is not None, (
        "hub._samsung_iot_headers should be set after Samsung IoT token refresh"
    )
    assert "iot-fresh-access-token" in hub._samsung_iot_headers.get("Authorization", ""), (
        f"Expected 'iot-fresh-access-token' in Authorization header, "
        f"got: {hub._samsung_iot_headers}"
    )


# ---------------------------------------------------------------------------
# Unit test: reauth routing
# ---------------------------------------------------------------------------

def _make_flow_for_reauth(entry):
    """Build a ConfigFlow with reauth entry wired up."""
    flow = ConfigFlow()
    flow.hass = _HomeAssistant()
    flow.hass.config_entries = MagicMock()
    flow.hass.config_entries.async_entries = MagicMock(return_value=[])
    # Wire _get_reauth_entry to return our entry
    flow._get_reauth_entry = MagicMock(return_value=entry)
    return flow


@pytest.mark.asyncio
async def test_reauth_standalone_oauth_routes_to_standalone_step():
    """async_step_reauth with standalone_oauth entry must route to
    async_step_reauth_standalone_oauth, NOT async_step_reauth_confirm."""
    entry = _make_standalone_entry()
    flow = _make_flow_for_reauth(entry)

    result = await flow.async_step_reauth(entry.data)

    assert result["step_id"] == "reauth_standalone_oauth", (
        f"Expected step_id='reauth_standalone_oauth', got {result['step_id']!r}. "
        "Standalone OAuth reauth must NOT route to reauth_confirm (the PAT step)."
    )


@pytest.mark.asyncio
async def test_reauth_pat_entry_routes_to_confirm_step():
    """async_step_reauth with PAT entry still routes to async_step_reauth_confirm."""
    from custom_components.samsung_familyhub_fridge.const import AUTH_MODE_PAT, CONF_TOKEN
    entry = _ConfigEntry(entry_id="pat-entry", data={
        CONF_AUTH_MODE: AUTH_MODE_PAT,
        CONF_TOKEN: "old-pat",
    })
    flow = _make_flow_for_reauth(entry)

    result = await flow.async_step_reauth(entry.data)

    assert result["step_id"] == "reauth_confirm", (
        f"PAT entries should still route to reauth_confirm, got {result['step_id']!r}"
    )


@pytest.mark.asyncio
async def test_reauth_standalone_oauth_full_flow(requests_mock):
    """Full reauth flow: credentials → link (raw code) → new refresh token saved."""
    entry = _make_standalone_entry()
    flow = _make_flow_for_reauth(entry)

    # Step 1: enter credentials
    result = await flow.async_step_reauth_standalone_oauth(
        user_input={
            CONF_OAUTH_CLIENT_ID: "new-client-id",
            CONF_OAUTH_CLIENT_SECRET: "new-client-secret",
        }
    )
    assert result["step_id"] == "reauth_standalone_oauth_link", (
        f"Expected link step after credentials, got: {result}"
    )

    # Stub token exchange
    requests_mock.post(
        TOKEN_URL,
        json={
            "access_token": "reauth-access-token",
            "refresh_token": "reauth-refresh-token",
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )

    # Step 2: provide auth code
    result = await flow.async_step_reauth_standalone_oauth_link(
        user_input={"redirect_url_or_code": "reauth-code-xyz"}
    )
    assert result["type"] == "abort", (
        f"Expected abort (reauth_successful) after code exchange, got: {result}"
    )
