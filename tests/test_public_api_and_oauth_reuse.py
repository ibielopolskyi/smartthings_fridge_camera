"""Tests for public-API device discovery and the OAuth-reuse config flow.

Covers:
- get_all_device_status uses the public api.smartthings.com/v1/devices
  endpoint (the private client.smartthings.com one rejects tokens without a
  Samsung ID) and follows pagination.
- set_device_id finds the fridge in the public API response format.
- authenticate() keeps the device list so device_id can be auto-detected.
- The "Reuse HA core SmartThings OAuth" step accepts Samsung Account
  credentials and stores the IoT refresh token for image downloads.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import DEVICES_URL, FamilyHub
from custom_components.samsung_familyhub_fridge.config_flow import (
    ConfigFlow,
    InvalidAuth,
)
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_OAUTH,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)

FLOW = "custom_components.samsung_familyhub_fridge.config_flow"

FRIDGE = {
    "deviceId": "fridge-1",
    "components": [
        {"id": "main", "capabilities": [{"id": "contactSensor"}]},
        {"id": "hub", "capabilities": [{"id": "samsungce.viewInside"}]},
    ],
}
TV = {
    "deviceId": "tv-1",
    "components": [{"id": "main", "capabilities": [{"id": "switch"}]}],
}


@pytest.fixture
def hub():
    hass = _HomeAssistant()
    return FamilyHub(hass, token="tok", device_id=None)


# ---------------------------------------------------------------------------
# api.py
# ---------------------------------------------------------------------------

def test_device_list_uses_public_api(hub):
    with rm.Mocker() as m:
        m.get(DEVICES_URL, json={"items": [FRIDGE]})
        data = hub.get_all_device_status()
    assert m.last_request.url.startswith("https://api.smartthings.com/v1/devices")
    assert m.last_request.headers["Authorization"] == "Bearer tok"
    assert data["items"] == [FRIDGE]


def test_device_list_follows_pagination(hub):
    page2 = "https://api.smartthings.com/v1/devices?page=1"
    with rm.Mocker() as m:
        m.get(DEVICES_URL, json={"items": [TV], "_links": {"next": {"href": page2}}})
        m.get(page2, json={"items": [FRIDGE], "_links": {}})
        data = hub.get_all_device_status()
    assert [d["deviceId"] for d in data["items"]] == ["tv-1", "fridge-1"]


def test_set_device_id_public_format(hub):
    hub.set_device_status({"items": [TV, FRIDGE]})
    assert hub.device_id == "fridge-1"


def test_set_device_id_public_format_no_fridge(hub):
    hub.set_device_status({"items": [TV]})
    assert hub.device_id is None


@pytest.mark.asyncio
async def test_authenticate_keeps_device_list_for_autodetect(hub):
    with rm.Mocker() as m:
        m.get(DEVICES_URL, json={"items": [TV, FRIDGE]})
        assert await hub.authenticate() is True
    assert hub.device_id == "fridge-1"


# ---------------------------------------------------------------------------
# config_flow.py — OAuth reuse step
# ---------------------------------------------------------------------------

def _make_flow():
    hass = _HomeAssistant()
    entry = MagicMock()
    entry.entry_id = "st-entry"
    entry.title = "SmartThings"
    entry.source = "user"
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    flow = ConfigFlow()
    flow.hass = hass
    flow.async_create_entry = lambda title, data: {
        "type": "create_entry", "title": title, "data": data,
    }
    return flow


def _validated(device_id="fridge-1"):
    return {
        CONF_AUTH_MODE: AUTH_MODE_OAUTH,
        CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
        CONF_DEVICE_ID: device_id,
    }


def _iot_creds():
    creds = MagicMock()
    creds.refresh_token = "iot-refresh"
    creds.auth_server_url = "https://us-auth2.samsungosp.com"
    return creds


@pytest.mark.asyncio
async def test_oauth_step_form_has_samsung_fields():
    flow = _make_flow()
    result = await flow.async_step_oauth()
    assert result["step_id"] == "oauth"
    keys = {str(k) for k in result["data_schema"].schema}
    assert {"samsung_email", "samsung_password", "samsung_signin_client_secret"} <= keys


@pytest.mark.asyncio
async def test_oauth_step_without_samsung_creates_entry():
    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated())):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DEVICE_ID] == "fridge-1"
    assert CONF_SAMSUNG_IOT_REFRESH_TOKEN not in result["data"]


@pytest.mark.asyncio
async def test_oauth_step_with_samsung_stores_iot_token():
    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated())), \
         patch(f"{FLOW}.SamsungAccountAuth") as MockAuth:
        MockAuth.return_value.login_iot.return_value = _iot_creds()
        result = await flow.async_step_oauth(
            user_input={
                CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
                "samsung_email": "user@example.com",
                "samsung_password": "pw",
            }
        )
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_OAUTH
    assert data[CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "iot-refresh"
    assert data[CONF_SAMSUNG_IOT_AUTH_SERVER] == "https://us-auth2.samsungosp.com"


@pytest.mark.asyncio
async def test_oauth_step_samsung_login_failure_shows_error():
    from custom_components.samsung_familyhub_fridge.auth import AuthError

    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated())), \
         patch(f"{FLOW}.SamsungAccountAuth") as MockAuth:
        MockAuth.return_value.login_iot.side_effect = AuthError("bad password")
        result = await flow.async_step_oauth(
            user_input={
                CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
                "samsung_email": "user@example.com",
                "samsung_password": "wrong",
            }
        )
    assert result["step_id"] == "oauth"
    assert result["errors"]["base"] == "samsung_login_failed"


@pytest.mark.asyncio
async def test_oauth_step_no_fridge_found_shows_error():
    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated(None))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result["step_id"] == "oauth"
    assert result["errors"]["base"] == "no_device_found"


@pytest.mark.asyncio
async def test_oauth_step_invalid_auth_shows_error():
    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(side_effect=InvalidAuth)):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result["errors"]["base"] == "invalid_auth"


# ---------------------------------------------------------------------------
# Samsung login rejected with HTTP 400 (reported from a real install)
# ---------------------------------------------------------------------------

def test_samsung_request_authentication_400_raises_auth_error():
    from custom_components.samsung_familyhub_fridge.auth import (
        SAMSUNG_AUTH_URL,
        AuthError,
        SamsungAccountAuth,
    )

    auth = SamsungAccountAuth(
        email="user@example.com",
        password="pw",
        signin_client_id="yfrtglt53o",
        signin_client_secret="",
    )
    with rm.Mocker() as m:
        m.post(
            SAMSUNG_AUTH_URL,
            status_code=400,
            json={"error": "invalid_request", "error_description": "bad client"},
        )
        with pytest.raises(AuthError, match="bad client"):
            auth.login_iot()


@pytest.mark.asyncio
async def test_oauth_step_samsung_http_400_shows_login_error_not_unknown():
    import requests

    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated())), \
         patch(f"{FLOW}.SamsungAccountAuth") as MockAuth:
        MockAuth.return_value.login_iot.side_effect = requests.HTTPError("400")
        result = await flow.async_step_oauth(
            user_input={
                CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
                "samsung_email": "user@example.com",
                "samsung_password": "pw",
            }
        )
    assert result["errors"]["base"] == "samsung_login_failed"


@pytest.mark.asyncio
async def test_coordinator_no_samsung_id_does_not_trigger_reauth():
    """OAuth token without Samsung login: image 400 must not force reauth."""
    from custom_components.samsung_familyhub_fridge.api import DataCoordinator

    hass = _HomeAssistant()
    hub = FamilyHub(hass, token="oauth-tok", device_id="fridge-1")
    hub.set_current_device_status(
        {"samsungce.viewInside": {"contents": {"value": [{"fileId": "f1"}]}}}
    )
    coordinator = DataCoordinator(hass, hub)
    with rm.Mocker() as m:
        m.get(
            rm.ANY,
            status_code=400,
            json={"error": {"code": "BadRequestError",
                            "message": "No samsung id available"}},
        )
        await coordinator._async_update_data()  # must not raise
    assert hub.images_unavailable_reason == "no_samsung_account"
    assert coordinator.last_file_ids == ["f1"]  # won't re-download every poll
