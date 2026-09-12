from __future__ import annotations

import io
import logging
from PIL import Image, ImageDraw

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api import FamilyHub, DataCoordinator

_LOGGER = logging.getLogger(__name__)


def _generate_placeholder_image(shelf_name: str) -> bytes:
    """Generate a clean dark placeholder JPEG when no photo has been snapped yet."""
    img = Image.new("RGB", (640, 480), color=(26, 29, 36))
    draw = ImageDraw.Draw(img)
    # Outer border
    draw.rectangle([(16, 16), (624, 464)], outline=(50, 56, 70), width=2)
    # Title text
    draw.text((40, 180), "Samsung Family Hub", fill=(240, 240, 240))
    draw.text((40, 220), shelf_name, fill=(100, 180, 255))
    draw.text((40, 270), "Open & close fridge door to snap photo", fill=(160, 160, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Family Hub fridge cameras."""
    hub: FamilyHub = hass.data[DOMAIN]["hub"]
    coordinator: DataCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            FamilyHubCamera("Family Hub Top", "family_hub_top", 0, hub, coordinator),
            FamilyHubCamera("Family Hub Middle", "family_hub_middle", 1, hub, coordinator),
            FamilyHubCamera("Family Hub Bottom", "family_hub_bottom", 2, hub, coordinator),
        ]
    )


class FamilyHubCamera(CoordinatorEntity, Camera):
    """Representation of a Samsung Family Hub shelf camera."""

    _attr_has_entity_name = False

    def __init__(
        self,
        friendly_name: str,
        key: str,
        index: int,
        hub: FamilyHub,
        coordinator: DataCoordinator,
    ):
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self.content_type = "image/jpeg"
        self.hub = hub
        self._index = index
        self._key = key
        self._name = friendly_name
        self._placeholder = _generate_placeholder_image(friendly_name)
        self._attr_unique_id = f"{hub.device_id}_{key}"

    @property
    def name(self) -> str:
        """Return the name of this camera."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return unique ID for Home Assistant."""
        return f"{self.hub.device_id}_{self._key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking cameras to the Family Hub device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub.device_id)},
            name="Samsung Family Hub",
            manufacturer="Samsung",
            model="Family Hub Refrigerator",
        )

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return image response."""
        if (
            self.hub.downloaded_images
            and self._index < len(self.hub.downloaded_images)
            and self.hub.downloaded_images[self._index]
            and self.hub.downloaded_images[self._index].startswith(b"\xff\xd8")
        ):
            return self.hub.downloaded_images[self._index]
        return self._placeholder

    @property
    def extra_state_attributes(self):
        """Return the camera state attributes."""
        return {
            "shelf_index": self._index,
            "device_id": self.hub.device_id,
        }
