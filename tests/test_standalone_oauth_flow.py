"""Unit tests for the standalone SmartThings OAuth config flow path."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest installs HA stubs before any component imports
from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_STANDALONE_OAUTH,
    CONF_AUTH_MODE,
    CONF_OAUTH_CLIENT_ID,
    CONF_OAUTH_CLIENT_SECRET,
    CONF_OAUTH_REFRESH_TOKEN,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)
from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(hass) -> ConfigFlow:
    """Return a ConfigFlow instance wired to a stub hass."""
    flow = ConfigFlow()
    flow.hass = hass
    return flow


def _fake_oauth_creds(access="tok-access", refresh="tok-refresh"):
    creds = MagicMock()
    creds.access_token = access
    creds.refresh_token = refresh
    return creds


def _fake_iot_creds(refresh="iot-refresh", auth_server="https://us-auth2.samsungosp.com"):
    creds = MagicMock()
    creds.refresh_token = refresh
    creds.auth_server_url = auth_server
    return creds


# ---------------------------------------------------------------------------
# async_step_user — menu wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_user_no_smartthings_entries_shows_standalone_and_pat():
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])

    flow = _make_flow(hass)
    result = await flow.async_step_user()

    assert result["type"] == "menu" or "menu_options" in result
    assert "standalone_oauth" in result["menu_options"]
    assert "pat" in result["menu_options"]


@pytest.mark.asyncio
async def test_step_user_with_smartthings_entries_shows_all_three():
    hass = _HomeAssistant()
    entry = MagicMock()
    entry.source = "user"
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])

    flow = _make_flow(hass)
    result = await flow.async_step_user()

    options = result["menu_options"]
    assert "oauth" in options
    assert "standalone_oauth" in options
    assert "pat" in options


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_credentials
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_credentials_step_empty_shows_form():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    result = await flow.async_step_standalone_oauth_credentials()
    assert result["type"] == "form"
    assert result["step_id"] == "standalone_oauth_credentials"


@pytest.mark.asyncio
async def test_credentials_step_missing_client_id_error():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    result = await flow.async_step_standalone_oauth_credentials(
        user_input={CONF_OAUTH_CLIENT_ID: "", CONF_OAUTH_CLIENT_SECRET: "secret"}
    )
    assert result["errors"].get(CONF_OAUTH_CLIENT_ID) == "required"


@pytest.mark.asyncio
async def test_credentials_step_missing_client_secret_error():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    result = await flow.async_step_standalone_oauth_credentials(
        user_input={CONF_OAUTH_CLIENT_ID: "my-client", CONF_OAUTH_CLIENT_SECRET: ""}
    )
    assert result["errors"].get(CONF_OAUTH_CLIENT_SECRET) == "required"


@pytest.mark.asyncio
async def test_credentials_step_valid_advances_to_link():
    hass = _HomeAssistant()
    flow = _make_flow(hass)

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth"
    ) as MockOAuth:
        mock_oauth = MagicMock()
        mock_oauth.get_authorization_url.return_value = "https://auth.smartthings.com/authorize?code_challenge=xyz"
        MockOAuth.return_value = mock_oauth

        result = await flow.async_step_standalone_oauth_credentials(
            user_input={
                CONF_OAUTH_CLIENT_ID: "my-client",
                CONF_OAUTH_CLIENT_SECRET: "my-secret",
            }
        )

    assert result["step_id"] == "standalone_oauth_link"
    assert flow._standalone_client_id == "my-client"
    assert flow._standalone_client_secret == "my-secret"
    assert flow._standalone_oauth is mock_oauth


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_link
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_link_step_shows_form_with_auth_url():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    oauth = MagicMock()
    oauth.get_authorization_url.return_value = "https://auth.example.com/go"
    flow._standalone_oauth = oauth
    flow._standalone_auth_url = "https://auth.example.com/go"

    result = await flow.async_step_standalone_oauth_link()

    assert result["step_id"] == "standalone_oauth_link"
    assert result["description_placeholders"]["authorization_url"] == "https://auth.example.com/go"


@pytest.mark.asyncio
async def test_link_step_empty_input_error():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    oauth = MagicMock()
    oauth.get_authorization_url.return_value = "https://auth.example.com/go"
    flow._standalone_oauth = oauth
    flow._standalone_auth_url = "https://auth.example.com/go"

    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": ""}
    )
    assert result["errors"]["redirect_url_or_code"] == "required"


@pytest.mark.asyncio
async def test_link_step_invalid_redirect_url_error():
    hass = _HomeAssistant()
    flow = _make_flow(hass)

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth"
    ) as MockOAuth:
        MockOAuth.extract_code_from_redirect.side_effect = ValueError("no code")
        oauth = MagicMock()
        oauth.get_authorization_url.return_value = "https://auth.example.com/go"
        flow._standalone_oauth = oauth
        flow._standalone_auth_url = "https://auth.example.com/go"

        result = await flow.async_step_standalone_oauth_link(
            user_input={"redirect_url_or_code": "https://httpbin.org/get?no_code=1"}
        )
    assert result["errors"]["redirect_url_or_code"] == "invalid_redirect_url"


@pytest.mark.asyncio
async def test_link_step_raw_code_advances():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    oauth_mock = MagicMock()
    oauth_mock.get_authorization_url.return_value = "https://auth.example.com/go"
    oauth_mock.exchange_code.return_value = _fake_oauth_creds()
    flow._standalone_oauth = oauth_mock
    flow._standalone_auth_url = "https://auth.example.com/go"

    result = await flow.async_step_standalone_oauth_link(
        user_input={"redirect_url_or_code": "raw-auth-code-abc"}
    )

    assert result["step_id"] == "standalone_oauth_samsung"
    assert flow._standalone_refresh_token == "tok-refresh"


@pytest.mark.asyncio
async def test_link_step_full_redirect_url_advances():
    hass = _HomeAssistant()
    flow = _make_flow(hass)

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth"
    ) as MockOAuth:
        MockOAuth.extract_code_from_redirect.return_value = "extracted-code"
        oauth_mock = MagicMock()
        oauth_mock.get_authorization_url.return_value = "https://auth.example.com/go"
        oauth_mock.exchange_code.return_value = _fake_oauth_creds()
        flow._standalone_oauth = oauth_mock
        flow._standalone_auth_url = "https://auth.example.com/go"

        result = await flow.async_step_standalone_oauth_link(
            user_input={"redirect_url_or_code": "https://httpbin.org/get?code=extracted-code"}
        )

    assert result["step_id"] == "standalone_oauth_samsung"


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_samsung
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_samsung_step_shows_form_on_first_call():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    flow._standalone_client_id = "cid"
    flow._standalone_client_secret = "csec"
    flow._standalone_refresh_token = "rt"

    result = await flow.async_step_standalone_oauth_samsung()

    assert result["step_id"] == "standalone_oauth_samsung"
    assert result["type"] == "form"


@pytest.mark.asyncio
async def test_samsung_step_skip_creates_entry_without_iot():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    flow._standalone_client_id = "cid"
    flow._standalone_client_secret = "csec"
    flow._standalone_refresh_token = "rt"
    created_entries = []
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "title": title, "data": data}

    result = await flow.async_step_standalone_oauth_samsung(
        user_input={"samsung_email": "", "samsung_password": ""}
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert result["data"][CONF_OAUTH_CLIENT_ID] == "cid"
    assert result["data"][CONF_OAUTH_REFRESH_TOKEN] == "rt"
    assert CONF_SAMSUNG_IOT_REFRESH_TOKEN not in result["data"]


@pytest.mark.asyncio
async def test_samsung_step_with_credentials_creates_entry_with_iot():
    hass = _HomeAssistant()
    flow = _make_flow(hass)
    flow._standalone_client_id = "cid"
    flow._standalone_client_secret = "csec"
    flow._standalone_refresh_token = "rt"
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "title": title, "data": data}

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth"
    ) as MockSamsungAuth:
        mock_auth = MagicMock()
        mock_auth.login_iot.return_value = _fake_iot_creds()
        MockSamsungAuth.return_value = mock_auth

        result = await flow.async_step_standalone_oauth_samsung(
            user_input={"samsung_email": "user@example.com", "samsung_password": "pass"}
        )

    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-refresh"
    assert data[CONF_SAMSUNG_IOT_AUTH_SERVER] == "https://us-auth2.samsungosp.com"


@pytest.mark.asyncio
async def test_samsung_step_login_failure_shows_error():
    from custom_components.samsung_familyhub_fridge.auth import AuthError

    hass = _HomeAssistant()
    flow = _make_flow(hass)
    flow._standalone_client_id = "cid"
    flow._standalone_client_secret = "csec"
    flow._standalone_refresh_token = "rt"

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth"
    ) as MockSamsungAuth:
        mock_auth = MagicMock()
        mock_auth.login_iot.side_effect = AuthError("bad credentials")
        MockSamsungAuth.return_value = mock_auth

        result = await flow.async_step_standalone_oauth_samsung(
            user_input={"samsung_email": "user@example.com", "samsung_password": "wrong"}
        )

    assert result["errors"]["base"] == "samsung_login_failed"
    assert result["step_id"] == "standalone_oauth_samsung"
