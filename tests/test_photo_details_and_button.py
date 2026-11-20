"""Tests for 'photo taken at' camera attributes and the refresh button."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import _HomeAssistant
from tests.test_camera_details_logging import _jpeg

from custom_components.samsung_familyhub_fridge.api import FamilyHub

# Shape copied from a real fridge's status (debug log).
VIEW_INSIDE = {
    "samsungce.viewInside": {
        "contents": {
            "timestamp": "2026-11-20T08:00:53.559Z",
            "value": [
                {"expiredTime": "2026-11-20T08:10:52Z", "fileId": "a",
                 "focusArea": "mainShelves", "mimeType": "image/jpeg"},
                {"expiredTime": "2026-11-20T08:10:52Z", "fileId": "b",
                 "focusArea": "mainShelves", "mimeType": "image/jpeg"},
                {"expiredTime": "2026-11-20T08:10:53Z", "fileId": "c",
                 "focusArea": "mainShelves", "mimeType": "image/jpeg"},
            ],
        },
        "lastUpdatedTime": {"timestamp": "2026-11-14T16:46:38.427Z",
                            "value": "2026-11-14T09:26:00Z"},
        "supportedFocusAreas": {"value": ["mainShelves"]},
    }
}


def _hub():
    hub = FamilyHub(_HomeAssistant(), token="t", device_id="fridge-1")
    hub.set_current_device_status(VIEW_INSIDE)
    return hub


def test_photo_details_has_taken_at_and_link_info():
    hub = _hub()
    hub.downloaded_images = [None, _jpeg(1062, 420), None]
    details = hub.photo_details(1)
    assert details == {
        "photo_taken_at": "2026-11-14T09:26:00Z",
        "links_issued_at": "2026-11-20T08:00:53.559Z",
        "focus_area": "mainShelves",
        "link_expires_at": "2026-11-20T08:10:52Z",
        "width": 1062,
        "height": 420,
    }


def test_photo_details_without_status_is_empty():
    hub = FamilyHub(_HomeAssistant(), token="t", device_id="fridge-1")
    assert hub.photo_details(0) == {}


def test_camera_attributes_come_from_hub():
    from custom_components.samsung_familyhub_fridge.camera import FamilyHubCamera

    coordinator = MagicMock()
    entry = MagicMock(entry_id="entry-1", data={"device_id": "fridge-1"})
    cam = FamilyHubCamera("family_hub_top", "Top", 0, _hub(), coordinator, entry)
    assert cam.coordinator is coordinator  # attributes refresh with each poll
    assert cam.extra_state_attributes["photo_taken_at"] == "2026-11-14T09:26:00Z"


@pytest.mark.asyncio
async def test_refresh_button_sends_refresh_command():
    from custom_components.samsung_familyhub_fridge.button import (
        FamilyHubRefreshButton,
    )

    hub = _hub()
    hub.update_camera = MagicMock()
    hub.should_update = True
    button = FamilyHubRefreshButton(
        hub, MagicMock(entry_id="entry-1", data={"device_id": "fridge-1"})
    )
    assert button._attr_unique_id == "entry-1_refresh_pictures"
    await button.async_press()
    hub.update_camera.assert_called_once()
    assert hub.should_update is False


@pytest.mark.asyncio
async def test_button_platform_setup_adds_button():
    from custom_components.samsung_familyhub_fridge.button import async_setup_entry

    hass = _HomeAssistant()
    hass.data = {"samsung_familyhub_fridge": {"entry-1": {"hub": _hub()}}}
    entry = MagicMock(entry_id="entry-1", data={"device_id": "fridge-1"})
    added = []
    await async_setup_entry(hass, entry, added.extend)
    assert len(added) == 1
