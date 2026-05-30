"""Integration tests for Samsung IoT token in-session refresh (SCRUM-85 / Task D).

Tests:
1. Happy path: IoT token + refresh token; 401 on first download triggers silent
   refresh; second download returns JPEG; download_images() returns True and
   the config entry's samsung_iot_refresh_token is updated.
2. No-refresh-token: IoT token set but no refresh token; 401 raises
   AuthenticationError immediately with no retry.
"""

import pytest
import requests_mock as rm

from unittest.mock import MagicMock

from tests.conftest import _ConfigEntry, _HomeAssistant
from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
)
from custom_components.samsung_familyhub_fridge.const import (
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)

SAMSUNG_IOT_REFRESH_URL = "https://us-auth2.samsungosp.com/auth/oauth2/token"
DOWNLOAD_URL_RE = r"https://client\.smartthings\.com/udo/file_links/.+"

# Minimal JPEG-like bytes (JPEG magic + padding)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 96


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hass():
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


def _make_hub(hass, entry, *, with_refresh_token: bool):
    hub = FamilyHub(hass, token="st-token", device_id="device-abc")
    if with_refresh_token:
        hub.set_samsung_iot_token(
            "iot-access-token",
            refresh_token="iot-refresh-token",
            auth_server="https://us-auth2.samsungosp.com",
            entry=entry,
        )
    else:
        hub.set_samsung_iot_token("iot-access-token")  # no refresh token
    hub._current_device_status = {
        "samsungce.viewInside": {
            "contents": {"value": [{"fileId": "file-id-1"}]}
        }
    }
    return hub


# ---------------------------------------------------------------------------
# Test 1: happy path — 401 triggers refresh, retry returns JPEG
# ---------------------------------------------------------------------------


def test_iot_refresh_on_401_retry_succeeds():
    """IoT token+refresh token: 401 triggers refresh, retry delivers JPEG."""
    hass = _make_hass()
    entry = _ConfigEntry(
        entry_id="entry-refresh-1",
        data={CONF_SAMSUNG_IOT_REFRESH_TOKEN: "iot-refresh-token"},
    )
    hub = _make_hub(hass, entry, with_refresh_token=True)

    with rm.Mocker() as m:
        m.get(
            rm.ANY,
            [
                {
                    "status_code": 401,
                    "json": {"error": "Unauthorized"},
                },
                {
                    "status_code": 200,
                    "content": JPEG_BYTES,
                    "headers": {"content-type": "image/jpeg"},
                },
            ],
        )
        m.post(
            SAMSUNG_IOT_REFRESH_URL,
            json={
                "access_token": "fresh-iot-access-token",
                "refresh_token": "fresh-iot-refresh-token",
            },
        )
        result = hub.download_images()

    assert result is True, "download_images() should return True after successful retry"

    # Token updated in-place
    assert hub._samsung_iot_token == "fresh-iot-access-token"
    assert hub._samsung_iot_refresh_token == "fresh-iot-refresh-token"
    assert hub._samsung_iot_headers["Authorization"] == "Bearer fresh-iot-access-token"

    # New refresh token persisted to config entry
    hass.config_entries.async_update_entry.assert_called_once()
    call_kwargs = hass.config_entries.async_update_entry.call_args[1]
    assert call_kwargs["data"][CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "fresh-iot-refresh-token"


# ---------------------------------------------------------------------------
# Test 2: no refresh token — AuthenticationError raised immediately, no retry
# ---------------------------------------------------------------------------


def test_iot_no_refresh_token_raises_auth_error_immediately():
    """IoT token but no refresh token: 401 raises AuthenticationError with no retry."""
    hass = _make_hass()
    entry = _ConfigEntry(entry_id="entry-norefresh", data={})
    hub = _make_hub(hass, entry, with_refresh_token=False)

    request_count = 0

    def _count_and_401(request, context):
        nonlocal request_count
        request_count += 1
        context.status_code = 401
        return {"error": "Unauthorized"}

    with rm.Mocker() as m:
        m.get(rm.ANY, json=_count_and_401)
        with pytest.raises(AuthenticationError):
            hub.download_images()

    assert request_count == 1, (
        f"Expected exactly 1 request (no retry), got {request_count}"
    )
    hass.config_entries.async_update_entry.assert_not_called()
