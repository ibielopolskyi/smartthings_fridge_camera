"""Refresh button for the Samsung Family Hub fridge cameras."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import fridge_device_info, legacy_entity_id, runtime_data


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up this fridge's refresh button."""
    hub = runtime_data(hass, config_entry)["hub"]
    async_add_entities([FamilyHubRefreshButton(hub, config_entry)])


class FamilyHubRefreshButton(ButtonEntity):
    """Ask the fridge to republish its latest camera photos."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera-retake"
    _attr_name = "Refresh pictures"

    def __init__(self, hub, config_entry) -> None:
        self.hub = hub
        self._attr_unique_id = f"{config_entry.entry_id}_refresh_pictures"
        self._attr_device_info = fridge_device_info(config_entry)
        legacy = legacy_entity_id(
            config_entry, "button", "family_hub_refresh_pictures"
        )
        if legacy:
            self.entity_id = legacy

    async def async_press(self) -> None:
        """Send the refresh command; new pictures arrive on the next poll."""
        await self.hub.async_request_refresh()
