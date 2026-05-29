"""Unit tests for the standalone SmartThings OAuth config flow steps."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

def _make_flow(hass=None):
    """Return a ConfigFlow instance wired up with a minimal hass stub."""
    if hass is None:
        hass = _HomeAssistant()
    flow = ConfigFlow()
    flow.hass = hass
    flow.async_show_form = MagicMock(
        side_effect=lambda **kw: {"type": "form", **kw}
    )
    flow.async_show_menu = MagicMock(
        side_effect=lambda **kw: {"type": "menu", **kw}
    )
    flow.async_create_entry = MagicMock(
        side_effect=lambda **kw: {"type": "create_entry", **kw}
    )
    return flow


def _fake_oauth(auth_url="https://api.smartthings.com/oauth/authorize?code=x"):
    """Return a mock SmartThingsOAuth with preset return values."""
    oauth = MagicMock()
    oauth.get_authorization_url.return_value = auth_url
    oauth.client_id = "test-client-id"
    oauth.client_secret = "test-client-secret"
    creds = MagicMock()
    creds.access_token = "test-access-token"
    creds.refresh_token = "test-refresh-token"
    oauth.exchange_code.return_value = creds
    return oauth


def _fake_iot_creds():
    iot = MagicMock()
    iot.refresh_token = "iot-refresh"
    iot.auth_server_url = "https://us-auth2.samsungosp.com"
    return iot


# ---------------------------------------------------------------------------
# async_step_user — menu contains standalone_oauth option
# ---------------------------------------------------------------------------

class TestUserStepMenu:
    @pytest.mark.asyncio
    async def test_menu_includes_standalone_oauth_with_smartthings_entries(self):
        flow = _make_flow()
        # Patch _smartthings_entries to return a non-empty list
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow._smartthings_entries",
            return_value=[MagicMock()],
        ):
            result = await flow.async_step_user()
        options = result["menu_options"]
        assert "standalone_oauth" in options
        assert "oauth" in options
        assert "pat" in options

    @pytest.mark.asyncio
    async def test_menu_includes_standalone_oauth_without_smartthings_entries(self):
        flow = _make_flow()
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow._smartthings_entries",
            return_value=[],
        ):
            result = await flow.async_step_user()
        options = result["menu_options"]
        assert "standalone_oauth" in options
        assert "pat" in options
        # oauth (reuse) not shown when no HA core entry exists
        assert "oauth" not in options


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_credentials
# ---------------------------------------------------------------------------

class TestCredentialsStep:
    @pytest.mark.asyncio
    async def test_shows_form_when_called_with_none(self):
        flow = _make_flow()
        result = await flow.async_step_standalone_oauth_credentials(None)
        assert result["type"] == "form"
        assert result["step_id"] == "standalone_oauth_credentials"

    @pytest.mark.asyncio
    async def test_empty_client_id_gives_error(self):
        flow = _make_flow()
        result = await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )
        assert result["type"] == "form"
        assert CONF_OAUTH_CLIENT_ID in result["errors"]

    @pytest.mark.asyncio
    async def test_empty_client_secret_gives_error(self):
        flow = _make_flow()
        result = await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: ""}
        )
        assert result["type"] == "form"
        assert CONF_OAUTH_CLIENT_SECRET in result["errors"]

    @pytest.mark.asyncio
    async def test_valid_credentials_advance_to_link_step(self):
        flow = _make_flow()
        mock_oauth = _fake_oauth()
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
            return_value=mock_oauth,
        ):
            result = await flow.async_step_standalone_oauth_credentials(
                {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
            )
        # Should have advanced to link step (shows its form)
        assert result["type"] == "form"
        assert result["step_id"] == "standalone_oauth_link"

    @pytest.mark.asyncio
    async def test_stores_code_verifier_in_flow_state(self):
        flow = _make_flow()
        mock_oauth = _fake_oauth()
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
            return_value=mock_oauth,
        ):
            await flow.async_step_standalone_oauth_credentials(
                {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
            )
        assert flow._standalone_oauth is mock_oauth
        assert flow._standalone_auth_url == mock_oauth.get_authorization_url.return_value

    @pytest.mark.asyncio
    async def test_oauth_construction_failure_shows_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
            side_effect=Exception("boom"),
        ):
            result = await flow.async_step_standalone_oauth_credentials(
                {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
            )
        assert result["type"] == "form"
        assert result["errors"].get("base") == "unknown"


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_link
# ---------------------------------------------------------------------------

class TestLinkStep:
    def _flow_after_credentials(self):
        flow = _make_flow()
        flow._standalone_oauth = _fake_oauth()
        flow._standalone_auth_url = "https://example.com/auth"
        return flow

    @pytest.mark.asyncio
    async def test_shows_form_when_called_with_none(self):
        flow = self._flow_after_credentials()
        result = await flow.async_step_standalone_oauth_link(None)
        assert result["type"] == "form"
        assert result["step_id"] == "standalone_oauth_link"

    @pytest.mark.asyncio
    async def test_auth_url_in_description_placeholders(self):
        flow = self._flow_after_credentials()
        result = await flow.async_step_standalone_oauth_link(None)
        assert result["description_placeholders"]["auth_url"] == "https://example.com/auth"

    @pytest.mark.asyncio
    async def test_empty_input_gives_error(self):
        flow = self._flow_after_credentials()
        result = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": ""}
        )
        assert result["type"] == "form"
        assert result["errors"].get("redirect_url_or_code") == "required"

    @pytest.mark.asyncio
    async def test_raw_code_accepted_and_exchanges(self):
        flow = self._flow_after_credentials()
        result = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": "rawcode123"}
        )
        flow._standalone_oauth.exchange_code.assert_called_once_with("rawcode123")
        assert result["type"] == "form"
        assert result["step_id"] == "standalone_oauth_samsung"

    @pytest.mark.asyncio
    async def test_full_redirect_url_parsed(self):
        flow = self._flow_after_credentials()
        flow._standalone_oauth.extract_code_from_redirect = MagicMock(
            return_value="extracted-code"
        )
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth"
                   ".extract_code_from_redirect",
            return_value="extracted-code",
        ):
            result = await flow.async_step_standalone_oauth_link(
                {"redirect_url_or_code": "https://httpbin.org/get?code=extracted-code"}
            )
        flow._standalone_oauth.exchange_code.assert_called_once_with("extracted-code")

    @pytest.mark.asyncio
    async def test_invalid_url_shows_error(self):
        flow = self._flow_after_credentials()
        flow._standalone_oauth.exchange_code.side_effect = ValueError("no code")
        result = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": "https://example.com/no-code-here"}
        )
        assert result["errors"].get("redirect_url_or_code") == "invalid_code"

    @pytest.mark.asyncio
    async def test_exchange_exception_shows_error(self):
        flow = self._flow_after_credentials()
        flow._standalone_oauth.exchange_code.side_effect = Exception("api down")
        result = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": "rawcode"}
        )
        assert result["errors"].get("redirect_url_or_code") == "invalid_code"

    @pytest.mark.asyncio
    async def test_tokens_stored_after_exchange(self):
        flow = self._flow_after_credentials()
        await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": "rawcode123"}
        )
        assert flow._standalone_access_token == "test-access-token"
        assert flow._standalone_refresh_token == "test-refresh-token"


# ---------------------------------------------------------------------------
# async_step_standalone_oauth_samsung
# ---------------------------------------------------------------------------

class TestSamsungStep:
    def _flow_after_link(self):
        flow = _make_flow()
        flow._standalone_oauth = _fake_oauth()
        flow._standalone_client_id = "cid"
        flow._standalone_client_secret = "secret"
        flow._standalone_access_token = "at"
        flow._standalone_refresh_token = "rt"
        return flow

    @pytest.mark.asyncio
    async def test_shows_form_when_called_with_none(self):
        flow = self._flow_after_link()
        result = await flow.async_step_standalone_oauth_samsung(None)
        assert result["type"] == "form"
        assert result["step_id"] == "standalone_oauth_samsung"

    @pytest.mark.asyncio
    async def test_skipping_samsung_creates_entry_without_iot(self):
        flow = self._flow_after_link()
        result = await flow.async_step_standalone_oauth_samsung(
            {"samsung_email": "", "samsung_password": ""}
        )
        assert result["type"] == "create_entry"
        data = result["data"]
        assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
        assert data[CONF_OAUTH_CLIENT_ID] == "cid"
        assert data[CONF_OAUTH_CLIENT_SECRET] == "secret"
        assert data[CONF_OAUTH_REFRESH_TOKEN] == "rt"
        assert CONF_SAMSUNG_IOT_REFRESH_TOKEN not in data
        assert CONF_SAMSUNG_IOT_AUTH_SERVER not in data

    @pytest.mark.asyncio
    async def test_samsung_login_success_adds_iot_tokens(self):
        flow = self._flow_after_link()
        iot_creds = _fake_iot_creds()
        mock_auth = MagicMock()
        mock_auth.login_iot.return_value = iot_creds

        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth",
            return_value=mock_auth,
        ):
            result = await flow.async_step_standalone_oauth_samsung(
                {"samsung_email": "user@example.com", "samsung_password": "pass"}
            )

        assert result["type"] == "create_entry"
        data = result["data"]
        assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-refresh"
        assert data[CONF_SAMSUNG_IOT_AUTH_SERVER] == "https://us-auth2.samsungosp.com"

    @pytest.mark.asyncio
    async def test_samsung_login_failure_shows_error(self):
        flow = self._flow_after_link()
        with patch(
            "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth",
            side_effect=Exception("auth failed"),
        ):
            result = await flow.async_step_standalone_oauth_samsung(
                {"samsung_email": "user@example.com", "samsung_password": "pass"}
            )
        assert result["type"] == "form"
        assert result["errors"].get("base") == "invalid_auth"

    @pytest.mark.asyncio
    async def test_partial_samsung_credentials_shows_error(self):
        flow = self._flow_after_link()
        result = await flow.async_step_standalone_oauth_samsung(
            {"samsung_email": "user@example.com", "samsung_password": ""}
        )
        assert result["type"] == "form"
        assert result["errors"].get("base") == "samsung_partial_credentials"

    @pytest.mark.asyncio
    async def test_entry_title_is_standalone_oauth(self):
        flow = self._flow_after_link()
        result = await flow.async_step_standalone_oauth_samsung(
            {"samsung_email": "", "samsung_password": ""}
        )
        assert "Standalone OAuth" in result["title"]
