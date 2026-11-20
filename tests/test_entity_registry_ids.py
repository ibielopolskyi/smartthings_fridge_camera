"""Every entity needs a unique ID and the shared fridge device."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tests.conftest import _HomeAssistant
from tests.test_photo_details_and_button import _hub

DOMAIN = "samsung_familyhub_fridge"


def _entry():
    entry = MagicMock(entry_id="entry-1")
    entry.data = {"device_id": "fridge-1"}
    return entry


async def _setup(platform):
    hass = _HomeAssistant()
    coordinator = MagicMock(last_updated_at=None)
    hass.data = {DOMAIN: {"entry-1": {"hub": _hub(), "coordinator": coordinator}}}
    added = []
    await platform.async_setup_entry(hass, _entry(), added.extend)
    return added, coordinator


@pytest.mark.asyncio
async def test_all_entities_have_unique_ids_and_one_device():
    from custom_components.samsung_familyhub_fridge import button, camera, sensor

    entities = []
    for platform in (button, camera, sensor):
        added, _ = await _setup(platform)
        entities += added
    assert len(entities) == 5
    ids = [e._attr_unique_id for e in entities]
    assert all(ids) and len(set(ids)) == 5
    devices = {frozenset(e._attr_device_info["identifiers"]) for e in entities}
    assert devices == {frozenset({(DOMAIN, "fridge-1")})}


@pytest.mark.asyncio
async def test_sensor_reports_download_time():
    from custom_components.samsung_familyhub_fridge import sensor

    added, coordinator = await _setup(sensor)
    s = added[0]
    assert s.native_value is None
    coordinator.last_updated_at = time.time()
    assert s.native_value.tzinfo is not None
