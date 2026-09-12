from __future__ import annotations

from datetime import datetime, timezone
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api import DataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN]["hub"]
    coordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([LastUpdatedAt(coordinator, hub)])


class LastUpdatedAt(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = False

    def __init__(self, coordinator, hub):
        self._last_updated_at = None
        self.hub = hub
        super().__init__(coordinator)
        self._attr_name = "Family Hub Cameras Last Updated"
        self._attr_unique_id = f"{hub.device_id}_last_updated_at"

    @property
    def unique_id(self) -> str:
        return f"{self.hub.device_id}_last_updated_at"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub.device_id)},
            name="Samsung Family Hub",
            manufacturer="Samsung",
            model="Family Hub Refrigerator",
        )

    @property
    def native_value(self) -> datetime | None:
        if self._last_updated_at:
            if isinstance(self._last_updated_at, (int, float)):
                return datetime.fromtimestamp(self._last_updated_at, tz=timezone.utc)
            return self._last_updated_at
        return None

    @property
    def last_updated_at(self) -> str | None:
        return str(self._last_updated_at) if self._last_updated_at else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._last_updated_at = self.coordinator.last_updated_at
        if self._last_updated_at:
            self.async_write_ha_state()
