from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api import DataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN]["hub"]
    coordinator = DataCoordinator(hass, hub)
    async_add_entities([LastUpdatedAt(coordinator)])


class LastUpdatedAt(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator):
        self._last_updated_at = None
        super().__init__(coordinator)

    @property
    def last_updated_at(self) -> str | None:
        return self._last_updated_at

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._last_updated_at = self.coordinator.last_updated_at
        if self._last_updated_at:
            self.async_write_ha_state()
