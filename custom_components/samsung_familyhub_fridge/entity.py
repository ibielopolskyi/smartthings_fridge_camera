"""Shared device info so all entities group under one fridge device."""
from __future__ import annotations

from .const import CONF_DEVICE_ID, DOMAIN


def fridge_device_info(config_entry) -> dict:
    """Device registry info for the fridge behind this config entry."""
    device_id = config_entry.data.get(CONF_DEVICE_ID) or config_entry.entry_id
    return {
        "identifiers": {(DOMAIN, device_id)},
        "name": "Samsung Family Hub Fridge",
        "manufacturer": "Samsung",
        "model": "Family Hub",
    }
