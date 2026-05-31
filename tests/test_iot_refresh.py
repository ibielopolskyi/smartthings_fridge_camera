"""Integration tests for Samsung IoT token in-session refresh (SCRUM-84).

Verifies that download_images() silently refreshes an expired Samsung IoT
token and retries the download rather than surfacing ConfigEntryAuthFailed.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tests.conftest import _HomeAssistant, _ConfigEntry
from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
)
from custom_components.samsung_familyhub_fridge.const import (
    CID,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
)

# Minimal device status that produces one file_id
DEVICE_STATUS = {
    "samsungce.viewInside": {
        "contents": {"value": [{"fileId": "file-abc123"}]}
    }
}
DEVICE_ID = "device-123"
FILE_ID = "file-abc123"
SAMSUNG_IOT_REFRESH_URL = "https://us-auth2.samsungosp.com/auth/oauth2/token"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def _file_link_url(file_id: str = FILE_ID, device_id: str = DEVICE_ID) -> str:
    return (
        f"https://client.smartthings.com/udo/file_links/{file_id}"
        f"?cid={CID}&di={device_id}"
    )


def _make_hass():
    hass = _HomeAssistant()
    hass.config_entries = MagicMock()
    return hass


# ---------------------------------------------------------------------------
# Test 1: hub has IoT token + refresh token — 401 → refresh → retry → success
# ---------------------------------------------------------------------------


class TestIoTRefreshOnAuthFailure:
    """401 during download triggers silent refresh and successful retry."""

    def test_download_retries_after_401_and_persists_new_refresh_token(
        self, requests_mock
    ):
        """401 on first IoT download → refresh → retry returns image → True."""
        hass = _make_hass()
        entry = _ConfigEntry(
            entry_id="iot-refresh-test",
            data={
                CONF_SAMSUNG_IOT_REFRESH_TOKEN: "old-iot-refresh-token",
                "samsung_iot_auth_server": "https://us-auth2.samsungosp.com",
            },
        )
        hub = FamilyHub(hass, token="st-token", device_id=DEVICE_ID)
        hub._current_device_status = DEVICE_STATUS
        hub.set_samsung_iot_token(
            "old-iot-access-token",
            refresh_token="old-iot-refresh-token",
            auth_server="https://us-auth2.samsungosp.com",
            entry=entry,
        )

        url = _file_link_url()
        # First call: 401 (token expired); second call: 200 + JPEG (after refresh)
        requests_mock.get(url, [
            {"status_code": 401, "json": {"error": "Unauthorized"}},
            {
                "status_code": 200,
                "content": JPEG_BYTES,
                "headers": {"content-type": "image/jpeg"},
            },
        ])
        # Samsung IoT refresh endpoint returns new tokens
        requests_mock.post(
            SAMSUNG_IOT_REFRESH_URL,
            json={
                "access_token": "new-iot-access-token",
                "refresh_token": "new-iot-refresh-token",
            },
        )

        result = hub.download_images()

        assert result is True, "download_images() must return True on successful retry"
        assert hub._samsung_iot_refresh_token == "new-iot-refresh-token", (
            "In-memory refresh token must be updated after successful refresh"
        )
        # Config entry must be persisted with the new refresh token
        hass.config_entries.async_update_entry.assert_called_once()
        _, kwargs = hass.config_entries.async_update_entry.call_args
        assert kwargs["data"][CONF_SAMSUNG_IOT_REFRESH_TOKEN] == "new-iot-refresh-token", (
            "Config entry data must contain the rotated samsung_iot_refresh_token"
        )

    def test_download_retries_after_400_no_samsung_id(self, requests_mock):
        """400 with 'No samsung id' triggers refresh and retry."""
        hass = _make_hass()
        entry = _ConfigEntry(
            entry_id="iot-refresh-400",
            data={CONF_SAMSUNG_IOT_REFRESH_TOKEN: "old-refresh"},
        )
        hub = FamilyHub(hass, token="st-token", device_id=DEVICE_ID)
        hub._current_device_status = DEVICE_STATUS
        hub.set_samsung_iot_token(
            "old-iot-token",
            refresh_token="old-refresh",
            auth_server="https://us-auth2.samsungosp.com",
            entry=entry,
        )

        url = _file_link_url()
        requests_mock.get(url, [
            {
                "status_code": 400,
                "json": {
                    "error": "BadRequestError",
                    "message": "No samsung id available",
                },
            },
            {
                "status_code": 200,
                "content": JPEG_BYTES,
                "headers": {"content-type": "image/jpeg"},
            },
        ])
        requests_mock.post(
            SAMSUNG_IOT_REFRESH_URL,
            json={
                "access_token": "new-iot-access",
                "refresh_token": "new-refresh",
            },
        )

        result = hub.download_images()

        assert result is True
        assert hub._samsung_iot_refresh_token == "new-refresh"


# ---------------------------------------------------------------------------
# Test 2: hub has no _samsung_iot_refresh_token — 401 must raise immediately
# ---------------------------------------------------------------------------


class TestIoTAuthFailureWithoutRefreshToken:
    """No refresh token: 401 raises AuthenticationError immediately, no retry."""

    def test_401_raises_auth_error_without_retry(self, requests_mock):
        """Without a refresh token, 401 raises AuthenticationError after one request."""
        hass = _make_hass()
        hub = FamilyHub(hass, token="st-token", device_id=DEVICE_ID)
        hub._current_device_status = DEVICE_STATUS
        # Set IoT token but NO refresh_token
        hub.set_samsung_iot_token("iot-access-only")

        url = _file_link_url()
        requests_mock.get(url, status_code=401)

        with pytest.raises(AuthenticationError):
            hub.download_images()

        # Exactly one HTTP GET made — no retry attempted
        assert requests_mock.call_count == 1, (
            "Must not retry when no refresh token is available"
        )
