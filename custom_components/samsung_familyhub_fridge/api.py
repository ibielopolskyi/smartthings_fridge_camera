from __future__ import annotations
from datetime import timedelta
import logging
import time
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
import requests

from homeassistant.core import HomeAssistant

from .const import CID, DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when SmartThings API returns an authentication error (401/403)."""


class DataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: FamilyHub):
        super().__init__(
            hass,
            _LOGGER,
            name="File ID refresher",
            update_interval=timedelta(seconds=10),
        )
        self._hass = hass
        self.api = api
        self.last_file_ids = []
        self.last_updated_at = None

    async def _async_update_data(self):
        """Fetch data from API endpoint."""
        try:
            if self.api.device_id is None:
                _LOGGER.debug("No device_id — fetching device list")
                status = await self._hass.async_add_executor_job(
                    self.api.get_all_device_status
                )
                self.api.set_device_status(status)
            if self.api.should_update:
                _LOGGER.debug("should_update=True → sending refresh command to fridge")
                await self._hass.async_add_executor_job(self.api.update_camera)
                self.api.should_update = False
            elif set(self.last_file_ids) != set(self.api.get_file_ids()):
                _LOGGER.debug(
                    "file IDs changed: %s → %s, downloading images",
                    self.last_file_ids,
                    self.api.get_file_ids(),
                )
                await self._hass.async_add_executor_job(self.api.download_images)
                self.last_updated_at = time.time()
                self.last_file_ids = self.api.get_file_ids()
            else:
                status = await self._hass.async_add_executor_job(
                    self.api.get_current_device_status
                )
                self.api.set_current_device_status(status)
                self.api.extract_device_data()
                _LOGGER.debug(
                    "polled status: last_closed=%s should_update=%s file_ids=%s",
                    self.api.last_closed,
                    self.api.should_update,
                    self.api.get_file_ids(),
                )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "SmartThings token expired or is invalid. "
                "Please re-authenticate with a new token."
            ) from err


class FamilyHub:
    """SmartThings Family Hub fridge API client."""

    def __init__(self, hass: HomeAssistant, token: str, device_id: str) -> None:
        """Initialize."""
        self._device_id = device_id
        self._hass = hass
        self.token = token
        self._headers = {"Authorization": f"Bearer {self.token}"}
        self.images = []
        self._device_status = None
        self._current_device_status = None
        self.last_closed = None
        self.should_update = False
        self.downloaded_images = [None, None, None]

    def update_token(self, token: str) -> None:
        """Update the API token (used after re-authentication)."""
        self.token = token
        self._headers = {"Authorization": f"Bearer {self.token}"}

    @property
    def device_id(self):
        if not self._device_id:
            self.set_device_id()
        return self._device_id

    def _check_response(self, response: requests.Response) -> None:
        """Check HTTP response for auth errors and raise accordingly."""
        if response.status_code in (401, 403):
            _LOGGER.error(
                "SmartThings authentication failed (HTTP %s). "
                "Token may have expired — SmartThings personal access tokens "
                "expire after 24 hours",
                response.status_code,
            )
            raise AuthenticationError(
                f"SmartThings API returned HTTP {response.status_code}. "
                "Token is expired or invalid."
            )
        if not response.ok:
            _LOGGER.warning(
                "SmartThings API request failed: HTTP %s - %s",
                response.status_code,
                response.text[:200],
            )

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        await self._hass.async_add_executor_job(self.get_all_device_status)
        return True

    def set_device_status(self, status):
        self._device_status = status

    def set_current_device_status(self, status):
        self._current_device_status = status

    def download_images(self):
        """Download the actual camera images from SmartThings."""
        if not self._current_device_status or not self.device_id:
            return [None, None, None]
        result = []
        for idx, file_id in enumerate(self.get_file_ids()):
            url = (
                f"https://client.smartthings.com/udo/file_links/{file_id}"
                f"?cid={CID}&di={self.device_id}"
            )
            r = requests.get(
                url,
                headers=self._headers,
                timeout=DEFAULT_TIMEOUT,
            )
            self._check_response(r)
            content_type = r.headers.get("content-type", "")
            _LOGGER.debug(
                "download_images[%d]: file_id=%s status=%s content_type=%s "
                "length=%d first_bytes=%r",
                idx,
                file_id[:8],
                r.status_code,
                content_type,
                len(r.content),
                r.content[:32],
            )
            # The file_links endpoint returns JSON with a signed URL, not the
            # image bytes directly. If we got JSON, follow it to fetch the
            # actual image.
            if "application/json" in content_type:
                try:
                    payload = r.json()
                    image_url = (
                        payload.get("url")
                        or payload.get("fileUrl")
                        or payload.get("downloadUrl")
                    )
                    _LOGGER.debug(
                        "download_images[%d]: JSON payload keys=%s image_url=%s",
                        idx,
                        list(payload.keys()) if isinstance(payload, dict) else None,
                        (image_url or "")[:120],
                    )
                    if image_url:
                        img_r = requests.get(image_url, timeout=DEFAULT_TIMEOUT)
                        _LOGGER.debug(
                            "download_images[%d]: followed URL → status=%s "
                            "length=%d",
                            idx,
                            img_r.status_code,
                            len(img_r.content),
                        )
                        result.append(img_r.content)
                        continue
                except Exception as err:
                    _LOGGER.warning(
                        "download_images[%d]: failed to parse JSON: %s",
                        idx,
                        err,
                    )
            result.append(r.content)
        self.downloaded_images = result
        _LOGGER.debug(
            "download_images: stored %d images, sizes=%s",
            len(result),
            [len(i) if i else 0 for i in result],
        )

    def get_all_device_status(self):
        """Get all of the devices in the account."""
        r = requests.get(
            "https://client.smartthings.com/devices/status",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        )
        self._check_response(r)
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            _LOGGER.error(
                "SmartThings API returned error: %s", data["error"]
            )
        return data

    def get_current_device_status(self):
        """Get the current device status."""
        r = requests.get(
            f"https://api.smartthings.com/v1/devices/{self.device_id}/components/main/status",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        )
        self._check_response(r)
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            _LOGGER.error(
                "SmartThings device status returned error: %s", data["error"]
            )
        return data

    def extract_device_data(self):
        """Extract contact sensor data to detect door close events."""
        if not self._current_device_status:
            return
        try:
            contact = self._current_device_status["contactSensor"]["contact"]
        except KeyError:
            _LOGGER.debug(
                "contactSensor data not available in device status"
            )
            return
        _LOGGER.debug(
            "contactSensor: value=%s timestamp=%s (last_closed=%s)",
            contact.get("value"),
            contact.get("timestamp"),
            self.last_closed,
        )
        # Trigger a refresh on first poll after startup, so users see fresh
        # images without having to physically open and close the fridge door.
        first_poll = self.last_closed is None
        if contact["value"] == "closed" and (
            first_poll or contact["timestamp"] != self.last_closed
        ):
            self.last_closed = contact["timestamp"]
            self.should_update = True
            if first_poll:
                _LOGGER.debug("First poll after startup — requesting camera refresh")

    def get_file_ids(self):
        """Get the file IDs for the camera images."""
        if not self._current_device_status:
            return []
        try:
            element = self._current_device_status["samsungce.viewInside"]["contents"]
            return [i["fileId"] for i in element["value"]]
        except (KeyError, TypeError):
            _LOGGER.debug(
                "samsungce.viewInside data not available in device status"
            )
            return []

    def set_device_id(self):
        """Extract device ID from the device status list."""
        if not self._device_status:
            return
        try:
            items = self._device_status["items"]
        except (KeyError, TypeError):
            _LOGGER.error(
                "Unexpected device status format — missing 'items' key. "
                "This may indicate an expired token or API error. "
                "Response: %s",
                str(self._device_status)[:200],
            )
            return
        for element in items:
            if (
                element.get("capabilityId") == "samsungce.viewInside"
                and element.get("attributeName") == "contents"
            ):
                self._device_id = element["deviceId"]
                break

    def update_camera(self):
        """Send a refresh command to the fridge camera."""
        if not self.device_id:
            return
        r = requests.post(
            f"https://api.smartthings.com/v1/devices/{self.device_id}/commands",
            headers=self._headers,
            json={
                "commands": [
                    {
                        "component": "main",
                        "capability": "execute",
                        "command": "execute",
                        "arguments": [
                            "/udo/contents/provider/vs/0",
                            {
                                "x.com.samsung.da.control": {
                                    "x.com.samsung.da.command": "refresh"
                                }
                            },
                        ],
                    }
                ]
            },
            timeout=DEFAULT_TIMEOUT,
        )
        self._check_response(r)

