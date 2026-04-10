"""The Samsung FamilyHub Fridge integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .api import FamilyHub
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

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

    async def _handle_refresh(call: ServiceCall) -> None:
        """Manually trigger a fridge camera refresh."""
        _LOGGER.info("Manual refresh requested — sending update_camera command")
        await hass.async_add_executor_job(hub.update_camera)
        # Also flag the coordinator to pick up new images on next poll
        hub.should_update = False  # already sent the command
        _LOGGER.info("Manual refresh command sent successfully")

    hass.services.async_register(DOMAIN, "refresh", _handle_refresh)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (e.g. after re-authentication)."""
    hub: FamilyHub = hass.data[DOMAIN]["hub"]
    hub.update_token(entry.data["token"])


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.services.async_remove(DOMAIN, "refresh")

    return unload_ok
