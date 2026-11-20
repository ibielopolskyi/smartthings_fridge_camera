from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import fridge_device_info, legacy_entity_id, runtime_data

# (legacy object id, name shown after the fridge's name, picture index)
CAMERAS = (
    ("family_hub_top", "Top", 0),
    ("family_hub_middle", "Middle", 1),
    ("family_hub_bottom", "Bottom", 2),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up this fridge's three shelf cameras."""
    data = runtime_data(hass, config_entry)
    async_add_entities(
        FamilyHubCamera(key, name, index, data["hub"], data["coordinator"], config_entry)
        for key, name, index in CAMERAS
    )


class FamilyHubCamera(CoordinatorEntity, Camera):
    """One fridge shelf picture; attributes refresh on every status poll."""

    _attr_has_entity_name = True

    def __init__(self, key, name, index, hub, coordinator, config_entry):
        super().__init__(coordinator)
        Camera.__init__(self)
        self.content_type = "image/jpeg"
        self.hub = hub
        self._index = index
        self._attr_name = name
        # Unique ID: links the camera to its config entry and lets users
        # rename / manage it in the UI.
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_device_info = fridge_device_info(config_entry)
        legacy = legacy_entity_id(config_entry, "camera", key)
        if legacy:
            self.entity_id = legacy

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return image response."""
        return self.hub.downloaded_images[self._index]

    @property
    def extra_state_attributes(self):
        """Samsung's details for this picture, e.g. when it was taken."""
        return self.hub.photo_details(self._index)
