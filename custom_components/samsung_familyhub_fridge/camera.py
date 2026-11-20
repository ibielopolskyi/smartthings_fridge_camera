from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DataCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Family Hub fridge cameras."""
    hub = hass.data[DOMAIN]["hub"]
    coordinator = hass.data[DOMAIN].get("coordinator") or DataCoordinator(hass, hub)
    async_add_entities(
        [
            FamilyHubCamera("family_hub_top", 0, hub, coordinator),
            FamilyHubCamera("family_hub_middle", 1, hub, coordinator),
            FamilyHubCamera("family_hub_bottom", 2, hub, coordinator),
        ]
    )


class FamilyHubCamera(CoordinatorEntity, Camera):
    """One fridge shelf picture; attributes refresh on every status poll."""

    def __init__(self, name, index, hub, coordinator):
        super().__init__(coordinator)
        Camera.__init__(self)
        self.content_type = "image/jpeg"
        self.hub = hub
        self._index = index
        self._name = name
        self._image = None

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return image response."""
        return self.hub.downloaded_images[self._index]

    @property
    def name(self):
        """Return the name of this camera."""
        return self._name

    @property
    def extra_state_attributes(self):
        """Samsung's details for this picture, e.g. when it was taken."""
        return self.hub.photo_details(self._index)
