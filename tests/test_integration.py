"""Integration tests that hit the real SmartThings API.

Run with:
    pytest tests/test_integration.py -m integration \
        --smartthings-token YOUR_TOKEN \
        --device-id YOUR_DEVICE_ID

All tests in this file are skipped unless --smartthings-token is provided.
--device-id is optional; tests that need it will be skipped individually.
"""

import pytest

from tests.conftest import _HomeAssistant

from custom_components.samsung_familyhub_fridge.api import (
    AuthenticationError,
    FamilyHub,
    DataCoordinator,
)
from custom_components.samsung_familyhub_fridge.const import CID


needs_token = pytest.mark.skipif(
    "not config.getoption('--smartthings-token')",
    reason="--smartthings-token not provided",
)
needs_device = pytest.mark.skipif(
    "not config.getoption('--device-id')",
    reason="--device-id not provided",
)


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass, smartthings_token, device_id):
    return FamilyHub(hass, token=smartthings_token, device_id=device_id or "")


# ---------------------------------------------------------------------------
# 1. Authentication against the real API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRealAuthentication:
    """Verify we can authenticate against the real SmartThings API."""

    @needs_token
    def test_valid_token_returns_data(self, hub):
        """A valid token should return a dict with 'items' from the devices endpoint."""
        result = hub.get_all_device_status()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "items" in result, (
            f"Expected 'items' key in response. Got keys: {list(result.keys())}"
        )

    @needs_token
    def test_invalid_token_raises_auth_error(self, hass):
        """A bogus token should trigger a 401/403 and raise AuthenticationError."""
        bad_hub = FamilyHub(hass, token="this-is-not-a-real-token", device_id="")
        with pytest.raises(AuthenticationError):
            bad_hub.get_all_device_status()

    @needs_token
    @pytest.mark.asyncio
    async def test_authenticate_method_succeeds(self, hub):
        """The async authenticate() helper should return True for a valid token."""
        result = await hub.authenticate()
        assert result is True


# ---------------------------------------------------------------------------
# 2. Device discovery
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeviceDiscovery:
    """Verify device discovery works against the real API."""

    @needs_token
    def test_device_list_has_entries(self, hub):
        """The items list should contain at least one device."""
        result = hub.get_all_device_status()
        assert len(result["items"]) > 0, "No devices found in account"

    @needs_token
    def test_auto_discover_device_id(self, hass, smartthings_token):
        """When no device_id is provided, set_device_id should find one."""
        hub = FamilyHub(hass, token=smartthings_token, device_id="")
        status = hub.get_all_device_status()
        hub.set_device_status(status)
        hub._device_id = None
        hub.set_device_id()
        # If the account has a Family Hub fridge, device_id should be set
        # If not, it stays None — both are valid outcomes, but it shouldn't crash
        if hub._device_id:
            assert isinstance(hub._device_id, str)
            assert len(hub._device_id) > 0


# ---------------------------------------------------------------------------
# 3. Device status and camera data
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeviceStatus:
    """Verify device status retrieval works against the real API."""

    @needs_token
    @needs_device
    def test_get_current_device_status(self, hub):
        """Should return a dict (the device's component/main status)."""
        result = hub.get_current_device_status()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        # Should not be an error response
        if "error" in result:
            pytest.skip(f"API returned error (device may be offline): {result['error']}")

    @needs_token
    @needs_device
    def test_extract_device_data_does_not_crash(self, hub):
        """extract_device_data should handle whatever the real API returns."""
        status = hub.get_current_device_status()
        hub.set_current_device_status(status)
        # Should not raise, regardless of what keys are present
        hub.extract_device_data()

    @needs_token
    @needs_device
    def test_get_file_ids_returns_list(self, hub):
        """get_file_ids should return a list (possibly empty if no images yet)."""
        status = hub.get_current_device_status()
        hub.set_current_device_status(status)
        file_ids = hub.get_file_ids()
        assert isinstance(file_ids, list)

    @needs_token
    @needs_device
    def test_download_images_when_available(self, hub):
        """If file IDs exist, download_images should fetch image bytes."""
        status = hub.get_current_device_status()
        hub.set_current_device_status(status)
        file_ids = hub.get_file_ids()
        if not file_ids:
            pytest.skip("No camera images available on device")
        hub.download_images()
        for i, img in enumerate(hub.downloaded_images):
            if img is not None:
                assert isinstance(img, bytes)
                assert len(img) > 0, f"Image {i} is empty"


# ---------------------------------------------------------------------------
# 4. Token expiry detection (real API)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTokenExpiryDetection:
    """Verify the full auth error → detection → recovery cycle against real API."""

    @needs_token
    def test_bad_token_detected_on_device_status(self, hass):
        bad_hub = FamilyHub(hass, token="expired-fake-token", device_id="any-device")
        with pytest.raises(AuthenticationError):
            bad_hub.get_current_device_status()

    @needs_token
    def test_bad_token_detected_on_update_camera(self, hass):
        bad_hub = FamilyHub(hass, token="expired-fake-token", device_id="any-device")
        with pytest.raises(AuthenticationError):
            bad_hub.update_camera()

    @needs_token
    def test_recovery_after_token_update(self, hass, smartthings_token):
        """Simulate: start with bad token → fail → update token → succeed."""
        hub = FamilyHub(hass, token="bad-token", device_id="")

        # 1. Fails with bad token
        with pytest.raises(AuthenticationError):
            hub.get_all_device_status()

        # 2. "Re-authenticate" with good token
        hub.update_token(smartthings_token)

        # 3. Now succeeds
        result = hub.get_all_device_status()
        assert isinstance(result, dict)
        assert "items" in result


# ---------------------------------------------------------------------------
# 5. Coordinator integration with real API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCoordinatorRealApi:
    """Verify the DataCoordinator works end-to-end with real API responses."""

    @needs_token
    @needs_device
    @pytest.mark.asyncio
    async def test_coordinator_full_poll_cycle(self, hass, smartthings_token, device_id):
        """Run one full coordinator poll cycle against the real API."""
        hub = FamilyHub(hass, token=smartthings_token, device_id=device_id)
        coordinator = DataCoordinator(hass, hub)

        # Pre-populate so coordinator takes the status-fetch path
        status = hub.get_current_device_status()
        hub.set_current_device_status(status)
        file_ids = hub.get_file_ids()
        coordinator.last_file_ids = file_ids

        # Should complete without raising
        await coordinator._async_update_data()

    @needs_token
    @pytest.mark.asyncio
    async def test_coordinator_bad_token_triggers_auth_failed(self, hass):
        """Coordinator should raise ConfigEntryAuthFailed with bad credentials."""
        from tests.conftest import _ConfigEntryAuthFailed

        hub = FamilyHub(hass, token="invalid-token", device_id="fake-device")
        coordinator = DataCoordinator(hass, hub)

        # Force the code path that calls get_current_device_status
        hub._device_id = "fake-device"
        hub.should_update = False
        hub._current_device_status = {
            "samsungce.viewInside": {"contents": {"value": [{"fileId": "x"}]}}
        }
        coordinator.last_file_ids = ["x"]

        with pytest.raises(_ConfigEntryAuthFailed):
            await coordinator._async_update_data()
