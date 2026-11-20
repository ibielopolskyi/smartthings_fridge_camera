"""Shared helpers so each fridge's entities group under its own device."""
from __future__ import annotations

from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN

DEFAULT_DEVICE_NAME = "Samsung Family Hub Fridge"


def runtime_data(hass, config_entry) -> dict:
    """The hub and coordinator stored for this fridge's config entry."""
    return hass.data[DOMAIN][config_entry.entry_id]


def fridge_device_info(config_entry) -> dict:
    """Device registry info for the fridge behind this config entry."""
    device_id = config_entry.data.get(CONF_DEVICE_ID) or config_entry.entry_id
    return {
        "identifiers": {(DOMAIN, device_id)},
        "name": config_entry.data.get(CONF_DEVICE_NAME) or DEFAULT_DEVICE_NAME,
        "manufacturer": "Samsung",
        "model": "Family Hub",
    }


def legacy_entity_id(config_entry, platform: str, object_id: str) -> str | None:
    """Entity ID used before multi-fridge support, for entries made then.

    Entries without a stored fridge name were created by older versions;
    keeping their entity IDs avoids breaking dashboards and automations.
    New entries get IDs from the fridge's name instead.
    """
    if config_entry.data.get(CONF_DEVICE_NAME):
        return None
    return f"{platform}.{object_id}"
