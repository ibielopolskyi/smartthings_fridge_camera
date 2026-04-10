from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api import FamilyHub


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Family Hub fridge cameras."""
    hub = hass.data[DOMAIN]["hub"]
    async_add_entities(
        [
            FamilyHubCamera("family_hub_top", 0, hub),
            FamilyHubCamera("family_hub_middle", 1, hub),
            FamilyHubCamera("family_hub_bottom", 2, hub),
        ]
    )


class FamilyHubCamera(Camera):
    def __init__(self, name, index, hub):
        super().__init__()
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
        """Return the camera state attributes."""
        return {}
