"""Tests for coordinator deadlock prevention, image validation, and camera entities."""

import pytest
import requests_mock as rm

from tests.conftest import _HomeAssistant
from custom_components.samsung_familyhub_fridge.api import DataCoordinator, FamilyHub
from custom_components.samsung_familyhub_fridge.camera import FamilyHubCamera
from custom_components.samsung_familyhub_fridge.sensor import LastUpdatedAt

DEVICE_ID = "test-device-123"
STATUS_URL = f"https://api.smartthings.com/v1/devices/{DEVICE_ID}/components/main/status"
COMMANDS_URL = f"https://api.smartthings.com/v1/devices/{DEVICE_ID}/commands"
DL_URL = "https://client.smartthings.com/udo/file_links/file-1?cid=5Hic3rk1FP&di=test-device-123"


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def hub(hass):
    return FamilyHub(hass, token="valid-token", device_id=DEVICE_ID)


class TestCoordinatorDeadlockFix:
    @pytest.mark.asyncio
    async def test_coordinator_does_not_deadlock_on_expired_file_ids(self, hass, hub):
        """When image downloads 404, file IDs are still tracked and coordinator continues polling status."""
        coordinator = DataCoordinator(hass, hub)
        coordinator.last_file_ids = []

        device_status = {
            "contactSensor": {"contact": {"value": "closed", "timestamp": "2026-09-12T00:00:00Z"}},
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "file-1"}]}
            }
        }

        with rm.Mocker() as m:
            m.get(STATUS_URL, json=device_status)
            m.post(COMMANDS_URL, json={})
            m.get(DL_URL, status_code=404, json={"error": "NotFoundError"})

            # First cycle: triggers download which 404s
            await coordinator._async_update_data()
            assert coordinator.last_file_ids == ["file-1"]

            # Second cycle: file IDs haven't changed, does not re-attempt dead download
            m.get(STATUS_URL, json=device_status)
            await coordinator._async_update_data()
            assert coordinator.last_file_ids == ["file-1"]


class TestDownloadImageValidation:
    def test_download_images_rejects_non_jpeg(self, hub):
        """Only responses with status 200 and JPEG magic bytes are stored."""
        hub._current_device_status = {
            "samsungce.viewInside": {
                "contents": {"value": [{"fileId": "file-1"}]}
            }
        }

        with rm.Mocker() as m:
            # Returns 200 but text/html, not a JPEG
            m.get(DL_URL, status_code=200, content=b"<html>error</html>")
            success = hub.download_images()
            assert success is False
            assert hub.downloaded_images[0] is None

        with rm.Mocker() as m:
            # Returns 200 with valid JPEG bytes
            valid_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
            m.get(DL_URL, status_code=200, content=valid_jpeg)
            success = hub.download_images()
            assert success is True
            assert hub.downloaded_images[0] == valid_jpeg


class TestCameraEntity:
    def test_camera_has_unique_id_and_device_info(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        cam = FamilyHubCamera("Family Hub Top", "family_hub_top", 0, hub, coordinator)

        assert cam.unique_id == f"{DEVICE_ID}_family_hub_top"
        assert cam.name == "Family Hub Top"
        assert cam.device_info["identifiers"] == {("samsung_familyhub_fridge", DEVICE_ID)}

    def test_camera_returns_placeholder_then_real_image(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        cam = FamilyHubCamera("Family Hub Top", "family_hub_top", 0, hub, coordinator)

        # Before download: returns placeholder JPEG
        img = cam.camera_image()
        assert img is not None
        assert img.startswith(b"\xff\xd8")

        # After download with valid JPEG
        valid_jpeg = b"\xff\xd8\xff\xe0" + b"\x11" * 20
        hub.downloaded_images = [valid_jpeg]
        assert cam.camera_image() == valid_jpeg


class TestSensorEntity:
    def test_sensor_has_unique_id_and_device_info(self, hass, hub):
        coordinator = DataCoordinator(hass, hub)
        sensor = LastUpdatedAt(coordinator, hub)
        assert sensor.unique_id == f"{DEVICE_ID}_last_updated_at"
        assert sensor.device_info["identifiers"] == {("samsung_familyhub_fridge", DEVICE_ID)}
