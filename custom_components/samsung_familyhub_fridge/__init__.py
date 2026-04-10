"""The Samsung FamilyHub Fridge integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import FamilyHub
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Samsung FamilyHub Fridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    hub = FamilyHub(
        hass, entry.data["token"], entry.data.get("device_id")
    )
    hass.data[DOMAIN][entry.entry_id] = entry
    hass.data[DOMAIN]["hub"] = hub

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (e.g. after re-authentication)."""
    hub: FamilyHub = hass.data[DOMAIN]["hub"]
    hub.update_token(entry.data["token"])


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
