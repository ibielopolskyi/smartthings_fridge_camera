from __future__ import annotations
from datetime import timedelta
import logging
import time
from typing import Any
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
import requests
import voluptuous as vol
import async_timeout

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CID, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: FamilyHub):
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="File ID refresher",
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=10),
        )
        self._hass = hass
        self.api = api
        self.last_file_ids = []
        self.last_updated_at = None

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10000):
                # Did we refresh on previous run?
                if self.api.should_update:
                    await self._hass.async_add_executor_job(self.api.update_camera)
                    self.api.should_update = False
                elif set(self.last_file_ids) != set(self.api.get_file_ids()):
                    await self._hass.async_add_executor_job(self.api.download_images)
                    self.last_updated_at = time.time()
                    self.last_file_ids = self.api.get_file_ids()
                else:
                    status = await self._hass.async_add_executor_job(
                        self.api.get_all_device_status
                    )
                    self.api.set_device_status(status)
                    self.api.extract_device_data()

        except Exception as err:
            raise err


class FamilyHub:
    """Placeholder class to make tests pass.

    TODO Remove this placeholder class and replace with things from your PyPI package.
    """

    def __init__(self, hass: HomeAssistant, token: str, device_id: str) -> None:
        """Initialize."""
        self._device_id = device_id
        self._hass = hass
        self.token = token
        self._headers = {"Authorization": f"Bearer {self.token}"}
        self.images = []
        self._device_status = None
        self.last_closed = None
        self.should_update = False
        self.downloaded_images = [None, None, None]

    @property
    def device_id(self):
        if not self._device_id:
            self.set_device_id()
        return self._device_id

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        await self._hass.async_add_executor_job(self.get_all_device_status)
        return True

    def set_device_status(self, status):
        self._device_status = status

    def download_images(self):
        """Download the actual camera image from smartthings.

        Saves the image to it's designated index
        """
        if not self._device_status or not self.device_id:
            return [None, None, None]
        result = []
        for file_id in self.get_file_ids():
            r = requests.get(
                f"https://client.smartthings.com/udo/file_links/{file_id}?cid={CID}&di={self.device_id}",
                headers=self._headers,
                timeout=DEFAULT_TIMEOUT,
            )
            result.append(r.content)
        self.downloaded_images = result

    def get_all_device_status(self):
        """Get all of the devices in the account.

        Main source of data about the current status (Too lazy to use the subscriptions)
        """
        return requests.get(
            "https://client.smartthings.com/devices/status",
            headers=self._headers,
            timeout=DEFAULT_TIMEOUT,
        ).json()

    def extract_device_data(self):
        if not self._device_status:
            return
        for element in self._device_status["items"]:
            if (
                element.get("deviceId") == self.device_id
                and element["componentId"] == "main"
            ):
                if (
                    element["capabilityId"] == "contactSensor"
                    and element["value"] == "closed"
                ):
                    if self.last_closed != element["timestamp"]:
                        self.last_closed = element["timestamp"]
                        self.should_update = True

    def get_file_ids(self):
        if not self._device_status:
            return []
        for element in self._device_status["items"]:
            if (
                element["capabilityId"] == "samsungce.viewInside"
                and element["attributeName"] == "contents"
            ):
                return [i["fileId"] for i in element["value"]]

    def set_device_id(self):
        if not self._device_status:
            return
        for element in self._device_status["items"]:
            if (
                element["capabilityId"] == "samsungce.viewInside"
                and element["attributeName"] == "contents"
            ):
                self._device_id = element["deviceId"]
                break

    def update_camera(self):
        if not self.device_id:
            return
        requests.post(
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
