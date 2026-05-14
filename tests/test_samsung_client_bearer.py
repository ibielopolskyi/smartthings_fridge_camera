"""Tests for Samsung client bearer image endpoint support."""

from __future__ import annotations

import logging

import pytest
import requests
import requests_mock as rm

from tests.conftest import _ConfigEntry, _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
    SamsungIdUnavailableError,
    normalize_bearer_token,
    redact_token,
)
from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow
from custom_components.samsung_familyhub_fridge.config_flow import OptionsFlowHandler
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_PAT,
    AUTH_MODE_SAMSUNG_CLIENT_BEARER,
    CONF_AUTH_MODE,
    CONF_CID,
    CONF_DEVICE_ID,
    CONF_TOKEN,
    SMARTTHINGS_DOMAIN,
)


DEVICE_URL = (
    "https://client.smartthings.com/devices/device-123"
    "?includeAllowedActions=true&includeChildren=true&includeStatus=true"
)
FILE_LINK_URL = (
    "https://client.smartthings.com/udo/file_links/file-abc"
    "?cid=cid-123&di=device-123"
)
CDN_URL = "https://download.samsungcloud.com/signed/file.jpg"


@pytest.fixture
def hass():
    return _HomeAssistant()


def _client_device_payload(file_id: str = "file-abc") -> dict:
    return {
        "status": {
            "components": {
                "main": {
                    "samsungce.viewInside": {
                        "contents": {"value": [{"fileId": file_id}]}
                    }
                }
            }
        }
    }


def test_bearer_token_input_is_normalized():
    assert normalize_bearer_token("Bearer abc123") == "abc123"
    assert normalize_bearer_token("bearer abc123") == "abc123"
    assert normalize_bearer_token(" abc123 ") == "abc123"


def test_redact_token_shows_only_edges():
    assert redact_token("Bearer abcdefgh12345678") == "abcd...5678"


def test_samsungce_viewinside_file_id_extraction_from_client_payload(hass):
    hub = FamilyHub(
        hass,
        "Bearer samsung-token",
        "device-123",
        auth_mode=AUTH_MODE_SAMSUNG_CLIENT_BEARER,
        cid="cid-123",
    )
    with rm.Mocker() as m:
        m.get(DEVICE_URL, json=_client_device_payload("file-xyz"))
        hub.set_current_device_status(hub.get_samsung_client_device_status())

    assert hub.get_file_ids() == ["file-xyz"]


def test_file_link_redirect_does_not_forward_authorization_to_cdn(hass):
    hub = FamilyHub(
        hass,
        "Bearer samsung-token",
        "device-123",
        auth_mode=AUTH_MODE_SAMSUNG_CLIENT_BEARER,
        cid="cid-123",
    )
    with rm.Mocker() as m:
        m.get(DEVICE_URL, json=_client_device_payload())
        m.get(FILE_LINK_URL, status_code=302, headers={"Location": CDN_URL})
        m.get(CDN_URL, content=b"\xff\xd8jpeg-bytes", headers={"Content-Type": "image/jpeg"})

        assert hub.download_images() is True

        file_link_request = m.request_history[1]
        cdn_request = m.request_history[2]
        assert file_link_request.headers["Authorization"] == "Bearer samsung-token"
        assert "Authorization" not in cdn_request.headers
        assert hub.downloaded_images[0] == b"\xff\xd8jpeg-bytes"


def test_no_samsung_id_response_raises_specific_error(hass):
    hub = FamilyHub(hass, "oauth-token", "device-123")
    with rm.Mocker() as m:
        m.get(
            "https://client.smartthings.com/devices/status",
            status_code=400,
            json={
                "error": {
                    "code": "BadRequestError",
                    "message": "No samsung id available",
                    "details": [],
                }
            },
        )
        with pytest.raises(SamsungIdUnavailableError):
            hub.get_all_device_status()


def test_401_and_403_raise_authentication_error(hass):
    hub = FamilyHub(hass, "samsung-token", "device-123")
    for status in (401, 403):
        with rm.Mocker() as m:
            m.get(DEVICE_URL, status_code=status)
            with pytest.raises(AuthenticationError):
                hub.get_samsung_client_device_status()


def test_failed_image_download_requires_jpeg(hass):
    hub = FamilyHub(
        hass,
        "samsung-token",
        "device-123",
        auth_mode=AUTH_MODE_SAMSUNG_CLIENT_BEARER,
        cid="cid-123",
    )
    with rm.Mocker() as m:
        m.get(DEVICE_URL, json=_client_device_payload())
        m.get(FILE_LINK_URL, status_code=302, headers={"Location": CDN_URL})
        m.get(CDN_URL, content=b"not jpeg", headers={"Content-Type": "text/plain"})

        assert hub.download_images() is False


def test_logs_redact_tokens(hass, caplog):
    hub = FamilyHub(hass, "abcdef1234567890", "device-123")
    response = requests.Response()
    response.status_code = 500
    response._content = b"Bearer abcdef1234567890 failed"  # pylint: disable=protected-access

    with caplog.at_level(logging.WARNING):
        hub._check_response(response)

    assert "abcdef1234567890" not in caplog.text
    assert "abcd...7890" in caplog.text


@pytest.mark.asyncio
async def test_config_flow_pat_mode_still_works(hass, monkeypatch):
    flow = ConfigFlow()
    flow.hass = hass

    async def validate_pat(_hass, data):
        return data

    monkeypatch.setattr(
        "custom_components.samsung_familyhub_fridge.config_flow._validate_pat",
        validate_pat,
    )
    result = await flow.async_step_pat(
        {CONF_TOKEN: "pat-token", CONF_DEVICE_ID: "device-123"}
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_MODE] == AUTH_MODE_PAT


@pytest.mark.asyncio
async def test_config_flow_oauth_mode_appears_when_smartthings_entry_exists(hass):
    hass.config_entries._entries.append(
        _ConfigEntry(entry_id="st-1", domain=SMARTTHINGS_DOMAIN, title="SmartThings")
    )
    flow = ConfigFlow()
    flow.hass = hass

    result = await flow.async_step_user()

    assert result["type"] == "menu"
    assert "oauth" in result["menu_options"]
    assert "samsung_client_bearer" in result["menu_options"]


@pytest.mark.asyncio
async def test_config_flow_samsung_client_bearer_requires_fields(hass):
    flow = ConfigFlow()
    flow.hass = hass

    result = await flow.async_step_samsung_client_bearer()

    assert result["type"] == "form"
    assert result["step_id"] == "samsung_client_bearer"
    schema = result["data_schema"].schema
    required_keys = {marker.schema for marker in schema}
    assert {CONF_TOKEN, CONF_DEVICE_ID, CONF_CID} <= required_keys


@pytest.mark.asyncio
async def test_options_flow_can_switch_oauth_entry_to_bearer_mode(hass, monkeypatch):
    entry = _ConfigEntry(
        data={CONF_AUTH_MODE: "oauth", CONF_DEVICE_ID: "old-device"},
    )
    flow = OptionsFlowHandler(entry)
    flow.hass = hass

    menu = await flow.async_step_init()
    assert "samsung_client_bearer" in menu["menu_options"]

    async def validate_bearer(_hass, data):
        return {**data, CONF_TOKEN: normalize_bearer_token(data[CONF_TOKEN])}

    monkeypatch.setattr(
        "custom_components.samsung_familyhub_fridge.config_flow._validate_samsung_client_bearer",
        validate_bearer,
    )
    result = await flow.async_step_samsung_client_bearer(
        {
            CONF_TOKEN: "Bearer new-token",
            CONF_DEVICE_ID: "device-123",
            CONF_CID: "cid-123",
        }
    )

    assert result["type"] == "create_entry"
    assert entry.data[CONF_AUTH_MODE] == AUTH_MODE_SAMSUNG_CLIENT_BEARER
    assert entry.data[CONF_TOKEN] == "new-token"
    assert entry.data[CONF_DEVICE_ID] == "device-123"
    assert entry.data[CONF_CID] == "cid-123"
