"""Refresh button for the Samsung Family Hub fridge cameras."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the refresh button."""
    hub = hass.data[DOMAIN]["hub"]
    async_add_entities([FamilyHubRefreshButton(hub, config_entry.entry_id)])


class FamilyHubRefreshButton(ButtonEntity):
    """Ask the fridge to republish its latest camera photos."""

    _attr_icon = "mdi:camera-retake"

    def __init__(self, hub, entry_id: str) -> None:
        self.hub = hub
        self._attr_name = "family_hub_refresh_pictures"
        self._attr_unique_id = f"{entry_id}_refresh_pictures"

    async def async_press(self) -> None:
        """Send the refresh command; new pictures arrive on the next poll."""
        await self.hub.async_request_refresh()
