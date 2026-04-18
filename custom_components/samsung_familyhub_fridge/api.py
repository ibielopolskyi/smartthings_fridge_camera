from __future__ import annotations
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
import requests

from homeassistant.core import HomeAssistant

from .const import CID, DEFAULT_TIMEOUT

if TYPE_CHECKING:
    from homeassistant.helpers import config_entry_oauth2_flow

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
            # OAuth mode: refresh the access token (if close to expiry) BEFORE
            # any API call. No-op for PAT mode.
            await self.api.async_ensure_fresh_token()
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
                new_ids = self.api.get_file_ids()
                _LOGGER.debug(
                    "file IDs changed: %s → %s, downloading images",
                    self.last_file_ids,
                    new_ids,
                )
                success = await self._hass.async_add_executor_job(
                    self.api.download_images
                )
                if success:
                    self.last_updated_at = time.time()
                    self.last_file_ids = new_ids
                else:
                    _LOGGER.warning(
                        "download_images returned no successes — will retry "
                        "on next poll"
                    )
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
    """SmartThings Family Hub fridge API client.

    Two auth modes:

    1. PAT mode (default): caller provides a raw SmartThings token via
       `token=`. Token is static; caller is responsible for refresh via
       `update_token()`.

    2. OAuth mode: after construction, caller attaches an
       ``OAuth2Session`` via `attach_oauth_session(session)`. Before every
       API call the coordinator awaits `async_ensure_fresh_token()` which
       asks HA's OAuth2Session to refresh the access token if it's close
       to expiry — no manual refresh needed.
    """

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
        self._oauth_session: "config_entry_oauth2_flow.OAuth2Session | None" = None

    def attach_oauth_session(
        self, session: "config_entry_oauth2_flow.OAuth2Session"
    ) -> None:
        """Bind an HA OAuth2Session so tokens refresh automatically.

        Once attached, `async_ensure_fresh_token()` consults this session
        before every API call and updates the bearer header in place.
        """
        self._oauth_session = session

    async def async_ensure_fresh_token(self) -> None:
        """If running in OAuth mode, ensure the bearer token is still valid.

        No-op for PAT mode. Safe to call on every poll — HA's OAuth2Session
        only performs a network refresh when the access_token is within
        a few seconds of expiring.
        """
        if self._oauth_session is None:
            return
        await self._oauth_session.async_ensure_token_valid()
        new_token = self._oauth_session.token.get("access_token")
        if new_token and new_token != self.token:
            self.update_token(new_token)

    def update_token(self, token: str) -> None:
        """Update the API token (used after re-authentication or OAuth refresh)."""
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

    def download_images(self) -> bool:
        """Download the actual camera images from SmartThings.

        Returns True if at least one image was downloaded successfully.
        Failed individual downloads preserve the previously-known image,
        so a transient network error on one image doesn't wipe the others.
        """
        if not self._current_device_status or not self.device_id:
            return False

        file_ids = self.get_file_ids()
        # Start from the existing images so a partial failure doesn't wipe
        # slots that we can't refresh this cycle.
        result = list(self.downloaded_images)
        while len(result) < len(file_ids):
            result.append(None)

        successes = 0
        for idx, file_id in enumerate(file_ids):
            try:
                # OAuth tokens work with the public API; PATs (Samsung-ID-
                # bearing) work with the internal client endpoint.
                if self._oauth_session is not None:
                    url = (
                        f"https://api.smartthings.com/v1/devices/"
                        f"{self.device_id}/files/{file_id}"
                    )
                else:
                    url = (
                        f"https://client.smartthings.com/udo/file_links/"
                        f"{file_id}?cid={CID}&di={self.device_id}"
                    )
                r = requests.get(
                    url,
                    headers=self._headers,
                    timeout=DEFAULT_TIMEOUT,
                )
                self._check_response(r)
                content_type = r.headers.get("content-type", "")
                _LOGGER.debug(
                    "download_images[%d]: file_id=%s url=%s status=%s "
                    "content_type=%s length=%d",
                    idx,
                    file_id[:8],
                    url.split("?")[0][-40:],
                    r.status_code,
                    content_type,
                    len(r.content),
                )
                result[idx] = r.content
                successes += 1
            except AuthenticationError:
                # Auth errors must propagate up so the coordinator can
                # trigger reauth — do not swallow.
                raise
            except Exception as err:
                _LOGGER.warning(
                    "download_images[%d]: failed to download file_id=%s: %s",
                    idx,
                    file_id[:8],
                    err,
                )
                # Keep the previous bytes for this slot (don't overwrite with None)

        self.downloaded_images = result
        _LOGGER.debug(
            "download_images: stored %d/%d images, sizes=%s",
            successes,
            len(file_ids),
            [len(i) if i else 0 for i in result],
        )
        return successes > 0

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
        """Send the reverse-engineered refresh command to the fridge.

        Uses the single OCF resource at /udo/contents/provider/vs/0 which
        contains all three camera images. Throttled by the coordinator via
        contactSensor door-close events.
        """
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
        _LOGGER.debug(
            "update_camera: status=%s body=%s",
            r.status_code,
            r.text[:300],
        )

