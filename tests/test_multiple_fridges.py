"""Multiple fridges: picking, no duplicates, separate runtime, targeted refresh."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests_mock as rm

from tests.conftest import _ConfigEntry, _HomeAssistant, _ServiceCall, _ServiceRegistry

from custom_components.samsung_familyhub_fridge.api import DEVICES_URL, FamilyHub
from custom_components.samsung_familyhub_fridge.const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PAT,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_TOKEN,
    DOMAIN,
)

FLOW = "custom_components.samsung_familyhub_fridge.config_flow"


def _fridge(device_id, label):
    return {
        "deviceId": device_id,
        "label": label,
        "components": [{"id": "main", "capabilities": [{"id": "samsungce.viewInside"}]}],
    }


TV = {"deviceId": "tv", "label": "TV",
      "components": [{"id": "main", "capabilities": [{"id": "switch"}]}]}


# ---------------------------------------------------------------------------
# FamilyHub.list_fridges
# ---------------------------------------------------------------------------

def test_list_fridges_returns_every_fridge_with_its_name():
    hub = FamilyHub(_HomeAssistant(), token="t", device_id=None)
    hub.set_device_status({"items": [_fridge("f1", "Kitchen"), TV, _fridge("f2", "Garage")]})
    assert hub.list_fridges() == {"f1": "Kitchen", "f2": "Garage"}


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

def _make_flow(existing_entries=()):
    from custom_components.samsung_familyhub_fridge.config_flow import ConfigFlow

    hass = _HomeAssistant()
    st_entry = MagicMock(entry_id="st-entry", title="SmartThings", source="user")

    def _entries(domain=None):
        if domain == DOMAIN:
            return list(existing_entries)
        return [st_entry]

    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(side_effect=_entries)
    flow = ConfigFlow()
    flow.hass = hass
    return flow


def _validated(fridges, device_id=None):
    return {
        CONF_AUTH_MODE: AUTH_MODE_OAUTH,
        CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
        CONF_DEVICE_ID: device_id,
        "_fridges": fridges,
    }


@pytest.mark.asyncio
async def test_two_fridges_asks_which_one_then_names_entry_after_it():
    flow = _make_flow()
    fridges = {"f1": "Kitchen Fridge", "f2": "Garage Fridge"}
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated(fridges))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result["step_id"] == "pick_fridge"
    assert result["data_schema"].schema[CONF_DEVICE_ID] == fridges

    result = await flow.async_step_pick_fridge({CONF_DEVICE_ID: "f2"})
    assert result["type"] == "create_entry"
    assert result["title"] == "Garage Fridge"
    assert result["data"][CONF_DEVICE_ID] == "f2"
    assert result["data"][CONF_DEVICE_NAME] == "Garage Fridge"
    assert "_fridges" not in result["data"]
    assert flow.unique_id == "f2"


@pytest.mark.asyncio
async def test_already_added_fridge_is_skipped():
    existing = _ConfigEntry("e1", {CONF_DEVICE_ID: "f1"}, unique_id="f1")
    flow = _make_flow([existing])
    fridges = {"f1": "Kitchen Fridge", "f2": "Garage Fridge"}
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated(fridges))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    # Only one fridge left → no question, it's picked automatically.
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DEVICE_ID] == "f2"


@pytest.mark.asyncio
async def test_all_fridges_added_aborts():
    existing = [
        _ConfigEntry("e1", {CONF_DEVICE_ID: "f1"}),  # legacy entry: no unique_id
        _ConfigEntry("e2", {CONF_DEVICE_ID: "f2"}, unique_id="f2"),
    ]
    flow = _make_flow(existing)
    fridges = {"f1": "Kitchen Fridge", "f2": "Garage Fridge"}
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated(fridges))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result == {"type": "abort", "reason": "already_configured"}


@pytest.mark.asyncio
async def test_typing_an_already_added_device_id_aborts():
    flow = _make_flow([_ConfigEntry("e1", {CONF_DEVICE_ID: "f1"})])
    with patch(f"{FLOW}._validate_oauth",
               AsyncMock(return_value=_validated({"f1": "Kitchen"}, device_id="f1"))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry",
                        CONF_DEVICE_ID: "f1"}
        )
    assert result == {"type": "abort", "reason": "already_configured"}


@pytest.mark.asyncio
async def test_pat_setup_lists_fridges_from_the_api():
    flow = _make_flow()
    with rm.Mocker() as m:
        m.get(DEVICES_URL, json={"items": [_fridge("f1", "Kitchen"), _fridge("f2", "Garage")]})
        result = await flow.async_step_pat({CONF_TOKEN: "pat"})
    assert result["step_id"] == "pick_fridge"
    result = await flow.async_step_pick_fridge({CONF_DEVICE_ID: "f1"})
    assert result["data"][CONF_AUTH_MODE] == AUTH_MODE_PAT
    assert result["title"] == "Kitchen"


@pytest.mark.asyncio
async def test_no_fridge_in_account_shows_error():
    flow = _make_flow()
    with patch(f"{FLOW}._validate_oauth", AsyncMock(return_value=_validated({}))):
        result = await flow.async_step_oauth(
            user_input={CONF_LINKED_SMARTTHINGS_ENTRY_ID: "st-entry"}
        )
    assert result["errors"]["base"] == "no_device_found"


# ---------------------------------------------------------------------------
# Runtime: separate hubs, unload one, targeted refresh
# ---------------------------------------------------------------------------

def _make_hass():
    hass = _HomeAssistant()
    hass.data = {}
    hass.services = _ServiceRegistry()
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


def _pat_entry(entry_id, device_id):
    return _ConfigEntry(
        entry_id,
        {CONF_AUTH_MODE: AUTH_MODE_PAT, CONF_TOKEN: "pat", CONF_DEVICE_ID: device_id,
         CONF_DEVICE_NAME: f"Fridge {device_id}"},
        unique_id=device_id,
        title=f"Fridge {device_id}",
    )


@pytest.mark.asyncio
async def test_two_fridges_run_side_by_side_and_unload_independently():
    from custom_components.samsung_familyhub_fridge import (
        async_setup_entry,
        async_unload_entry,
    )

    hass = _make_hass()
    e1, e2 = _pat_entry("e1", "f1"), _pat_entry("e2", "f2")
    assert await async_setup_entry(hass, e1)
    assert await async_setup_entry(hass, e2)
    hub1 = hass.data[DOMAIN]["e1"]["hub"]
    hub2 = hass.data[DOMAIN]["e2"]["hub"]
    assert hub1 is not hub2
    assert (hub1.device_id, hub2.device_id) == ("f1", "f2")
    assert hass.data[DOMAIN]["e1"]["coordinator"].api is hub1

    assert await async_unload_entry(hass, e1)
    assert "e1" not in hass.data[DOMAIN]
    assert hass.data[DOMAIN]["e2"]["hub"] is hub2
    assert hass.services.has_service(DOMAIN, "refresh")

    assert await async_unload_entry(hass, e2)
    assert not hass.services.has_service(DOMAIN, "refresh")


@pytest.mark.asyncio
async def test_refresh_action_targets_the_chosen_fridge_or_all():
    from custom_components.samsung_familyhub_fridge import async_setup_entry

    hass = _make_hass()
    await async_setup_entry(hass, _pat_entry("e1", "f1"))
    await async_setup_entry(hass, _pat_entry("e2", "f2"))
    hubs = {eid: hass.data[DOMAIN][eid]["hub"] for eid in ("e1", "e2")}
    for hub in hubs.values():
        hub.async_request_refresh = AsyncMock()
    handler = hass.services._services[(DOMAIN, "refresh")]

    registry = MagicMock()
    registry.async_get.side_effect = lambda dev: MagicMock(config_entries={"e2"}) if dev == "ha-dev-2" else None
    with patch("homeassistant.helpers.device_registry.async_get", return_value=registry):
        await handler(_ServiceCall({"device_id": ["ha-dev-2"]}))
    hubs["e1"].async_request_refresh.assert_not_called()
    hubs["e2"].async_request_refresh.assert_awaited_once()

    await handler(_ServiceCall({}))
    hubs["e1"].async_request_refresh.assert_awaited_once()
    assert hubs["e2"].async_request_refresh.await_count == 2


# ---------------------------------------------------------------------------
# Entities: names from the fridge, legacy IDs kept for old entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entities_named_after_fridge_and_legacy_ids_kept():
    from custom_components.samsung_familyhub_fridge import button, camera, sensor

    async def entities_for(entry):
        hass = _HomeAssistant()
        hub = FamilyHub(hass, token="t", device_id=entry.data[CONF_DEVICE_ID])
        hass.data = {DOMAIN: {entry.entry_id: {
            "hub": hub, "coordinator": MagicMock(last_updated_at=None)}}}
        added = []
        for platform in (camera, button, sensor):
            await platform.async_setup_entry(hass, entry, added.extend)
        return added

    new = await entities_for(_pat_entry("e2", "f2"))
    assert [e._attr_name for e in new] == [
        "Top", "Middle", "Bottom", "Refresh pictures", "Pictures downloaded"]
    assert all(e._attr_has_entity_name for e in new)
    assert all(e._attr_device_info["name"] == "Fridge f2" for e in new)
    assert all(getattr(e, "entity_id", None) is None for e in new)

    legacy_entry = _ConfigEntry("old", {CONF_AUTH_MODE: AUTH_MODE_PAT,
                                        CONF_DEVICE_ID: "f1"})
    legacy = await entities_for(legacy_entry)
    assert [e.entity_id for e in legacy] == [
        "camera.family_hub_top", "camera.family_hub_middle",
        "camera.family_hub_bottom", "button.family_hub_refresh_pictures",
        "sensor.family_hub_pictures_downloaded"]
