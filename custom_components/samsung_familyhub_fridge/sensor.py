from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import fridge_device_info, legacy_entity_id, runtime_data


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = runtime_data(hass, config_entry)["coordinator"]
    async_add_entities([LastUpdatedAt(coordinator, config_entry)])


class LastUpdatedAt(CoordinatorEntity, SensorEntity):
    """When Home Assistant last downloaded new fridge pictures."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_icon = "mdi:image-refresh"
    _attr_name = "Pictures downloaded"

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_pictures_downloaded"
        self._attr_device_info = fridge_device_info(config_entry)
        legacy = legacy_entity_id(
            config_entry, "sensor", "family_hub_pictures_downloaded"
        )
        if legacy:
            self.entity_id = legacy

    @property
    def native_value(self) -> datetime | None:
        ts = self.coordinator.last_updated_at
        return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
