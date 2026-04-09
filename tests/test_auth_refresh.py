"""Unit tests for auth refresh, error detection, and defensive error handling."""

import pytest
import requests_mock as rm

# conftest installs HA stubs before any component imports
from tests.conftest import _HomeAssistant, _ConfigEntryAuthFailed

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    DataCoordinator,
    FamilyHub,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass):
    return FamilyHub(hass, token="valid-token", device_id="device-123")


DEVICE_STATUS_URL = "https://client.smartthings.com/devices/status"
CURRENT_STATUS_URL = (
    "https://api.smartthings.com/v1/devices/device-123/components/main/status"
)
COMMANDS_URL = "https://api.smartthings.com/v1/devices/device-123/commands"
FILE_LINK_URL_RE = r"https://client\.smartthings\.com/udo/file_links/.+"


# ---------------------------------------------------------------------------
# 1. _check_response: auth error detection
# ---------------------------------------------------------------------------

class TestCheckResponse:
    """Verify _check_response raises AuthenticationError on 401/403."""

    def test_401_raises_auth_error(self, hub):
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, status_code=401, json={"error": "Unauthorized"})
            with pytest.raises(AuthenticationError, match="401"):
                hub.get_all_device_status()

    def test_403_raises_auth_error(self, hub):
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, status_code=403, json={"error": "Forbidden"})
            with pytest.raises(AuthenticationError, match="403"):
                hub.get_all_device_status()

    def test_200_does_not_raise(self, hub):
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, json={"items": []})
            result = hub.get_all_device_status()
            assert result == {"items": []}

    def test_500_does_not_raise_auth_error(self, hub):
        """Non-auth HTTP errors should not raise AuthenticationError."""
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, status_code=500, text="Internal Server Error")
            # Should not raise AuthenticationError (it logs a warning but returns)
            # However the .json() call will likely fail, so let's test _check_response directly
            resp = requests_response(500)
            hub._check_response(resp)  # no exception

    def test_current_device_status_401(self, hub):
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, status_code=401)
            with pytest.raises(AuthenticationError):
                hub.get_current_device_status()

    def test_update_camera_401(self, hub):
        with rm.Mocker() as m:
            m.post(COMMANDS_URL, status_code=401)
            with pytest.raises(AuthenticationError):
                hub.update_camera()

    def test_download_images_403(self, hub):
        hub._current_device_status = {
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "file-1"}]}
            }
        }
        with rm.Mocker() as m:
            m.get(rm.ANY, status_code=403)
            with pytest.raises(AuthenticationError):
                hub.download_images()


def requests_response(status_code, json_data=None):
    """Create a minimal requests.Response for direct _check_response testing."""
    import requests
    resp = requests.models.Response()
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# 2. Defensive KeyError handling
# ---------------------------------------------------------------------------

class TestDefensiveKeyAccess:
    """Verify methods handle missing keys gracefully instead of crashing."""

    def test_extract_device_data_missing_contact_sensor(self, hub):
        """Issue #7: missing contactSensor key should not crash."""
        hub._current_device_status = {"someOtherCapability": {}}
        # Should return gracefully, not raise KeyError
        hub.extract_device_data()
        assert hub.should_update is False

    def test_extract_device_data_none_status(self, hub):
        hub._current_device_status = None
        hub.extract_device_data()
        assert hub.should_update is False

    def test_get_file_ids_missing_view_inside(self, hub):
        """Issue #7: missing samsungce.viewInside should return empty list."""
        hub._current_device_status = {"someOtherCapability": {}}
        assert hub.get_file_ids() == []

    def test_get_file_ids_missing_contents(self, hub):
        hub._current_device_status = {"samsungce.viewInside": {}}
        assert hub.get_file_ids() == []

    def test_get_file_ids_none_status(self, hub):
        hub._current_device_status = None
        assert hub.get_file_ids() == []

    def test_get_file_ids_valid_data(self, hub):
        hub._current_device_status = {
            "samsungce.viewInside": {
                "contents": {
                    "value": [
                        {"fileId": "abc123"},
                        {"fileId": "def456"},
                        {"fileId": "ghi789"},
                    ]
                }
            }
        }
        assert hub.get_file_ids() == ["abc123", "def456", "ghi789"]

    def test_set_device_id_missing_items(self, hub):
        """Issue #5: API returns error object without 'items' key."""
        hub._device_id = None
        hub._device_status = {
            "error": {
                "code": "UnexpectedError",
                "message": "A non-recoverable error condition occurred.",
            }
        }
        hub.set_device_id()
        assert hub._device_id is None  # should not crash

    def test_set_device_id_none_status(self, hub):
        hub._device_id = None
        hub._device_status = None
        hub.set_device_id()
        assert hub._device_id is None

    def test_set_device_id_valid_data(self, hub):
        hub._device_id = None
        hub._device_status = {
            "items": [
                {
                    "capabilityId": "samsungce.viewInside",
                    "attributeName": "contents",
                    "deviceId": "my-fridge-id",
                },
            ]
        }
        hub.set_device_id()
        assert hub._device_id == "my-fridge-id"

    def test_extract_device_data_triggers_update_on_door_close(self, hub):
        hub._current_device_status = {
            "contactSensor": {
                "contact": {"value": "closed", "timestamp": "2025-01-01T00:00:00Z"}
            }
        }
        hub.last_closed = None
        hub.extract_device_data()
        assert hub.should_update is True
        assert hub.last_closed == "2025-01-01T00:00:00Z"

    def test_extract_device_data_no_update_on_same_timestamp(self, hub):
        hub._current_device_status = {
            "contactSensor": {
                "contact": {"value": "closed", "timestamp": "2025-01-01T00:00:00Z"}
            }
        }
        hub.last_closed = "2025-01-01T00:00:00Z"
        hub.should_update = False
        hub.extract_device_data()
        assert hub.should_update is False


# ---------------------------------------------------------------------------
# 3. Token update
# ---------------------------------------------------------------------------

class TestTokenUpdate:
    """Verify update_token refreshes the authorization header."""

    def test_update_token_changes_header(self, hub):
        assert hub._headers["Authorization"] == "Bearer valid-token"
        hub.update_token("new-token-abc")
        assert hub.token == "new-token-abc"
        assert hub._headers["Authorization"] == "Bearer new-token-abc"

    def test_update_token_used_in_api_calls(self, hub):
        hub.update_token("refreshed-token")
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, json={"items": []})
            hub.get_all_device_status()
            assert m.last_request.headers["Authorization"] == "Bearer refreshed-token"


# ---------------------------------------------------------------------------
# 4. API error response handling
# ---------------------------------------------------------------------------

class TestApiErrorResponses:
    """Verify API error responses are logged but don't crash."""

    def test_get_all_device_status_with_error_body(self, hub):
        error_resp = {
            "error": {
                "code": "UnexpectedError",
                "message": "A non-recoverable error condition occurred.",
            }
        }
        with rm.Mocker() as m:
            m.get(DEVICE_STATUS_URL, json=error_resp)
            result = hub.get_all_device_status()
            assert "error" in result

    def test_get_current_device_status_with_error_body(self, hub):
        error_resp = {
            "error": {
                "code": "UnexpectedError",
                "message": "Something went wrong.",
            }
        }
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, json=error_resp)
            result = hub.get_current_device_status()
            assert "error" in result


# ---------------------------------------------------------------------------
# 5. DataCoordinator auth error propagation
# ---------------------------------------------------------------------------

class TestCoordinatorAuthPropagation:
    """Verify coordinator converts AuthenticationError to ConfigEntryAuthFailed."""

    @pytest.mark.asyncio
    async def test_coordinator_raises_config_entry_auth_failed(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, status_code=401)
            # Pre-set state so coordinator takes the "else" path (get_current_device_status)
            hub._device_id = "device-123"
            hub.should_update = False
            hub._current_device_status = {
                "samsungce.viewInside": {
                    "contents": {"value": [{"fileId": "f1"}]}
                }
            }
            coordinator.last_file_ids = ["f1"]  # same file IDs -> triggers status fetch

            with pytest.raises(_ConfigEntryAuthFailed):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_coordinator_raises_on_update_camera_auth_error(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        hub._device_id = "device-123"
        hub.should_update = True
        with rm.Mocker() as m:
            m.post(COMMANDS_URL, status_code=403)
            with pytest.raises(_ConfigEntryAuthFailed):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_coordinator_normal_flow_no_error(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        hub._device_id = "device-123"
        hub.should_update = False
        hub._current_device_status = {
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "f1"}]}
            }
        }
        coordinator.last_file_ids = ["f1"]

        normal_status = {
            "contactSensor": {
                "contact": {"value": "open", "timestamp": "2025-01-01"}
            },
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "f1"}]}
            },
        }
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, json=normal_status)
            # Should complete without raising
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# 6. Full auth-expire-and-recover integration scenario
# ---------------------------------------------------------------------------

class TestFullAuthRefreshScenario:
    """End-to-end: token expires -> detected -> token updated -> works again."""

    def test_expire_detect_refresh_recover(self, hub):
        with rm.Mocker() as m:
            # 1. Initial call works
            m.get(DEVICE_STATUS_URL, json={"items": []})
            result = hub.get_all_device_status()
            assert result == {"items": []}

        with rm.Mocker() as m:
            # 2. Token expires -> 401
            m.get(DEVICE_STATUS_URL, status_code=401)
            with pytest.raises(AuthenticationError):
                hub.get_all_device_status()

        # 3. User re-authenticates, token is updated
        hub.update_token("brand-new-token")

        with rm.Mocker() as m:
            # 4. Subsequent call with new token works
            m.get(DEVICE_STATUS_URL, json={"items": [{"deviceId": "fridge-1"}]})
            result = hub.get_all_device_status()
            assert result["items"][0]["deviceId"] == "fridge-1"
            assert m.last_request.headers["Authorization"] == "Bearer brand-new-token"

    @pytest.mark.asyncio
    async def test_coordinator_recovers_after_token_update(self, hass):
        hub = FamilyHub(hass, token="old-token", device_id="device-123")
        coordinator = DataCoordinator(hass, hub)
        hub.should_update = False
        hub._current_device_status = {
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "f1"}]}
            }
        }
        coordinator.last_file_ids = ["f1"]

        # Step 1: Auth fails
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, status_code=401)
            with pytest.raises(_ConfigEntryAuthFailed):
                await coordinator._async_update_data()

        # Step 2: Token refreshed
        hub.update_token("new-token")

        # Step 3: Next poll succeeds
        normal_status = {
            "contactSensor": {
                "contact": {"value": "open", "timestamp": "2025-01-01"}
            },
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "f1"}]}
            },
        }
        with rm.Mocker() as m:
            m.get(CURRENT_STATUS_URL, json=normal_status)
            await coordinator._async_update_data()  # no exception
