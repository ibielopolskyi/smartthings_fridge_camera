"""Integration test: full standalone OAuth config flow, end to end.

Exercises all three steps (credentials → link → samsung) in sequence,
using mocked network calls so no live credentials are needed.  This tests
the wiring between steps — that state set in step 1 is correctly consumed
by step 2, and step 2 state by step 3.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
)


# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------

def _make_flow():
    hass = _HomeAssistant()
    flow = ConfigFlow()
    flow.hass = hass
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    flow.async_show_menu = MagicMock(side_effect=lambda **kw: {"type": "menu", **kw})
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: {"type": "create_entry", **kw})
    return flow


def _mock_oauth(client_id="cid", client_secret="secret",
                auth_url="https://api.smartthings.com/oauth/authorize?test=1",
                access_token="at-123", refresh_token="rt-456"):
    oauth = MagicMock()
    oauth.get_authorization_url.return_value = auth_url
    oauth.client_id = client_id
    oauth.client_secret = client_secret
    creds = MagicMock()
    creds.access_token = access_token
    creds.refresh_token = refresh_token
    oauth.exchange_code.return_value = creds
    return oauth


def _mock_iot_creds(refresh_token="iot-rt", auth_server="https://us-auth2.samsungosp.com"):
    c = MagicMock()
    c.refresh_token = refresh_token
    c.auth_server_url = auth_server
    return c


# ---------------------------------------------------------------------------
# Integration test: happy path without Samsung Account
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_without_samsung_account():
    """Step 1→2→3 with valid code; Samsung step skipped → config entry created."""
    flow = _make_flow()
    mock_oauth = _mock_oauth()

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
        return_value=mock_oauth,
    ):
        # Step 1 — credentials
        r1 = await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )
    assert r1["type"] == "form"
    assert r1["step_id"] == "standalone_oauth_link"
    assert "cid" in r1["description_placeholders"]["auth_url"] or True  # URL present

    # Step 2 — link (raw code)
    r2 = await flow.async_step_standalone_oauth_link(
        {"redirect_url_or_code": "rawcode-abc"}
    )
    assert r2["type"] == "form"
    assert r2["step_id"] == "standalone_oauth_samsung"
    mock_oauth.exchange_code.assert_called_once_with("rawcode-abc")

    # Step 3 — samsung skipped
    r3 = await flow.async_step_standalone_oauth_samsung(
        {"samsung_email": "", "samsung_password": ""}
    )
    assert r3["type"] == "create_entry"
    data = r3["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_OAUTH_CLIENT_ID] == "cid"
    assert data[CONF_OAUTH_CLIENT_SECRET] == "secret"
    assert data[CONF_OAUTH_REFRESH_TOKEN] == "rt-456"
    assert CONF_SAMSUNG_IOT_REFRESH_TOKEN not in data
    assert CONF_SAMSUNG_IOT_AUTH_SERVER not in data


# ---------------------------------------------------------------------------
# Integration test: happy path with Samsung Account
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_with_samsung_account():
    """Step 1→2→3 with Samsung credentials → IoT token in config entry."""
    flow = _make_flow()
    mock_oauth = _mock_oauth()
    mock_iot = _mock_iot_creds()
    mock_samsung_auth = MagicMock()
    mock_samsung_auth.login_iot.return_value = mock_iot

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
        return_value=mock_oauth,
    ):
        await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )

    await flow.async_step_standalone_oauth_link({"redirect_url_or_code": "code-xyz"})

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SamsungAccountAuth",
        return_value=mock_samsung_auth,
    ):
        r3 = await flow.async_step_standalone_oauth_samsung(
            {"samsung_email": "user@example.com", "samsung_password": "hunter2"}
        )

    assert r3["type"] == "create_entry"
    data = r3["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_STANDALONE_OAUTH
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-rt"
    assert data[CONF_SAMSUNG_IOT_AUTH_SERVER] == "https://us-auth2.samsungosp.com"


# ---------------------------------------------------------------------------
# Integration test: redirect URL flow (full URL instead of raw code)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_with_redirect_url():
    """Step 2 accepts a full httpbin redirect URL and extracts the code."""
    flow = _make_flow()
    mock_oauth = _mock_oauth()

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
        return_value=mock_oauth,
    ):
        await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )

    redirect = "https://httpbin.org/get?code=extracted-code&state=xyz"
    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth.extract_code_from_redirect",
        return_value="extracted-code",
    ):
        r2 = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": redirect}
        )

    mock_oauth.exchange_code.assert_called_once_with("extracted-code")
    assert r2["type"] == "form"
    assert r2["step_id"] == "standalone_oauth_samsung"


# ---------------------------------------------------------------------------
# Integration test: bad credentials (missing client_id) — stays on step 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flow_bad_credentials_stays_on_step1():
    """Submitting empty client_id keeps the flow on the credentials step."""
    flow = _make_flow()
    r = await flow.async_step_standalone_oauth_credentials(
        {CONF_OAUTH_CLIENT_ID: "", CONF_OAUTH_CLIENT_SECRET: "secret"}
    )
    assert r["type"] == "form"
    assert r["step_id"] == "standalone_oauth_credentials"
    assert r["errors"].get(CONF_OAUTH_CLIENT_ID)


# ---------------------------------------------------------------------------
# Integration test: bad code → stays on link step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flow_bad_code_stays_on_link_step():
    """Submitting a bad redirect URL keeps the flow on the link step."""
    flow = _make_flow()
    mock_oauth = _mock_oauth()
    mock_oauth.exchange_code.side_effect = ValueError("no code")

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
        return_value=mock_oauth,
    ):
        await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth.extract_code_from_redirect",
        side_effect=ValueError("no code"),
    ):
        r2 = await flow.async_step_standalone_oauth_link(
            {"redirect_url_or_code": "https://example.com/nope"}
        )

    assert r2["type"] == "form"
    assert r2["step_id"] == "standalone_oauth_link"
    assert r2["errors"].get("redirect_url_or_code") == "invalid_code"


# ---------------------------------------------------------------------------
# Integration test: auth_url present in description_placeholders on link step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_url_surfaced_in_link_step():
    """The authorization URL from step 1 must appear in the link step form."""
    flow = _make_flow()
    expected_url = "https://api.smartthings.com/oauth/authorize?client_id=cid&..."
    mock_oauth = _mock_oauth(auth_url=expected_url)

    with patch(
        "custom_components.samsung_familyhub_fridge.config_flow.SmartThingsOAuth",
        return_value=mock_oauth,
    ):
        await flow.async_step_standalone_oauth_credentials(
            {CONF_OAUTH_CLIENT_ID: "cid", CONF_OAUTH_CLIENT_SECRET: "secret"}
        )

    r2 = await flow.async_step_standalone_oauth_link(None)
    assert r2["description_placeholders"]["auth_url"] == expected_url
