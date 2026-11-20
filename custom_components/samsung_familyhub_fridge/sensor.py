from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DataCoordinator
from .const import DOMAIN
from .entity import fridge_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN].get("coordinator") or DataCoordinator(
        hass, hass.data[DOMAIN]["hub"]
    )
    async_add_entities([LastUpdatedAt(coordinator, config_entry)])


class LastUpdatedAt(CoordinatorEntity, SensorEntity):
    """When Home Assistant last downloaded new fridge pictures."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:image-refresh"

    def __init__(self, coordinator, config_entry=None):
        super().__init__(coordinator)
        self._attr_name = "family_hub_pictures_downloaded"
        if config_entry is not None:
            self._attr_unique_id = f"{config_entry.entry_id}_pictures_downloaded"
            self._attr_device_info = fridge_device_info(config_entry)

    @property
    def native_value(self) -> datetime | None:
        ts = self.coordinator.last_updated_at
        return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
