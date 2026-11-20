from __future__ import annotations
from datetime import timedelta
import json
import logging
import re
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

DEVICES_URL = "https://api.smartthings.com/v1/devices"
VIEW_INSIDE_CAPABILITY = "samsungce.viewInside"


class AuthenticationError(Exception):
    """Raised when SmartThings API returns an authentication error (401/403)."""


CAMERA_KEYWORDS = re.compile(r"view|image|camera|file|picture|photo|video", re.I)


def jpeg_size(data: bytes | None) -> tuple[int, int] | None:
    """Return (width, height) of a JPEG from its SOF header, or None."""
    if not data or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF15 except DHT(C4), JPG(C8), DAC(CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        i += 2 + length
    return None


def camera_related_status(full_status: dict) -> dict:
    """Pick every camera/image-looking capability from a full device status."""
    found: dict = {}
    components = (full_status or {}).get("components") or {}
    for comp_name, caps in components.items():
        if not isinstance(caps, dict):
            continue
        for cap_name, attrs in caps.items():
            if CAMERA_KEYWORDS.search(cap_name):
                found[f"{comp_name}/{cap_name}"] = attrs
    return found


class ImageTokenExpired(AuthenticationError):
    """The picture-only SmartThings token was rejected (expired/revoked)."""


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
        self._camera_details_logged = False
        self._last_view_inside_json: str | None = None

    def _log_view_inside_if_changed(self) -> None:
        """Debug-log the samsungce.viewInside block whenever it changes."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        status = self.api._current_device_status
        if not isinstance(status, dict):
            return
        block = status.get("samsungce.viewInside")
        text = json.dumps(block, sort_keys=True)
        if text != self._last_view_inside_json:
            self._last_view_inside_json = text
            _LOGGER.debug("Camera details: samsungce.viewInside = %s", text[:4000])

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
            if (
                not self._camera_details_logged
                and self.api.device_id
                and _LOGGER.isEnabledFor(logging.DEBUG)
            ):
                self._camera_details_logged = True
                await self._hass.async_add_executor_job(
                    self.api.log_camera_details
                )
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
                elif self.api.images_unavailable_reason:
                    # Already warned once; don't re-download every poll.
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
                self._log_view_inside_if_changed()
                self.api.extract_device_data()
                _LOGGER.debug(
                    "polled status: last_closed=%s should_update=%s file_ids=%s",
                    self.api.last_closed,
                    self.api.should_update,
                    self.api.get_file_ids(),
                )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                f"SmartThings authentication failed: {err}"
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
        # Samsung IoT token for client.smartthings.com image downloads.
        # Only set in OAuth mode; PAT tokens already carry Samsung ID.
        self._samsung_iot_token: str | None = None
        self._samsung_iot_headers: dict | None = None
        self._samsung_iot_refresh_token: str | None = None
        self._samsung_iot_auth_server: str = "https://us-auth2.samsungosp.com"
        self._entry = None
        # Set when the image server refused our token for lack of a Samsung
        # Account identity; stops retrying every poll until reload.
        self.images_unavailable_reason: str | None = None
        # Optional SmartThings PAT used only for image downloads.
        self._image_headers: dict | None = None
        # Set once the public files endpoint is confirmed to work.
        self._public_files_accept: str | None = None

    def set_samsung_iot_token(
        self,
        token: str,
        refresh_token: str | None = None,
        auth_server: str | None = None,
        entry=None,
    ) -> None:
        """Set a Samsung IoT token for client.smartthings.com image downloads.

        This token carries Samsung Account identity and is needed because
        the udo/file_links endpoint rejects standard SmartThings OAuth tokens.
        Optionally store a refresh_token, auth_server, and config entry so
        download_images() can silently refresh the token on auth failures.
        """
        self._samsung_iot_token = token
        self._samsung_iot_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.smartthings+json;v=1",
        }
        if refresh_token is not None:
            self._samsung_iot_refresh_token = refresh_token
        if auth_server is not None:
            self._samsung_iot_auth_server = auth_server
        if entry is not None:
            self._entry = entry

    def set_image_token(self, token: str | None) -> None:
        """Use a SmartThings personal access token only for picture downloads."""
        token = (token or "").strip()
        self._image_headers = (
            {"Authorization": f"Bearer {token}"} if token else None
        )
        # A new token deserves a fresh attempt.
        if self.images_unavailable_reason == "image_token_no_samsung_id":
            self.images_unavailable_reason = None

    # Accept headers to try on the public files endpoint, in order. It
    # answers 406 to image/* (seen in the wild), so it most likely returns
    # JSON describing the file rather than the raw bytes.
    _PUBLIC_FILES_ACCEPTS = (
        "application/vnd.smartthings+json;v=1",
        "application/json",
        "image/jpeg, image/*, application/octet-stream, */*",
    )

    @staticmethod
    def _looks_like_image(r: requests.Response) -> bool:
        content_type = r.headers.get("content-type", "")
        return (
            r.ok
            and bool(r.content)
            and not content_type.startswith(("application/json", "text/"))
            and "json" not in content_type
            and r.content[:1] not in (b"{", b"[", b"<")
        )

    @staticmethod
    def _find_urls(obj) -> list[str]:
        """Return every http(s) URL string found anywhere in a JSON value."""
        found: list[str] = []
        if isinstance(obj, dict):
            for value in obj.values():
                found.extend(FamilyHub._find_urls(value))
        elif isinstance(obj, list):
            for value in obj:
                found.extend(FamilyHub._find_urls(value))
        elif isinstance(obj, str) and obj.startswith(("https://", "http://")):
            found.append(obj)
        return found

    def _download_public_file(
        self, file_id: str, accept: str
    ) -> tuple[bytes | None, str]:
        """Fetch one picture via api.smartthings.com/v1/devices/{id}/files.

        Handles both a raw image reply and a JSON reply that links to the
        image. Returns (image bytes or None, diagnostic text for the log).
        """
        url = (
            f"https://api.smartthings.com/v1/devices/"
            f"{self.device_id}/files/{file_id}"
        )
        r = requests.get(
            url,
            headers={**self._headers, "Accept": accept},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code in (401, 403):
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
        if self._looks_like_image(r):
            return r.content, "image"
        content_type = r.headers.get("content-type", "")
        reply = f"HTTP {r.status_code} {content_type}: {r.text[:500]}"
        if not r.ok:
            return None, reply
        try:
            body = r.json()
        except ValueError:
            return None, reply
        links = self._find_urls(body)
        if not links:
            return None, f"{reply} (no download link in reply)"
        for link in links:
            # Signed links usually reject extra auth; SmartThings links
            # usually need it. Try both.
            for headers in ({}, self._headers):
                try:
                    lr = requests.get(link, headers=headers, timeout=DEFAULT_TIMEOUT)
                except requests.RequestException as err:
                    reply = f"{reply} | link error: {err}"
                    continue
                if self._looks_like_image(lr):
                    return lr.content, "image via link"
                reply = (
                    f"{reply} | link {link.split('?')[0]} -> HTTP "
                    f"{lr.status_code} {lr.headers.get('content-type', '')}"
                )
        return None, reply

    def _probe_public_files_api(self, file_id: str) -> bytes | None:
        """Find a way to get pictures from the public files endpoint.

        Tries each Accept header once. On success remembers it and returns
        the image bytes; otherwise marks images unavailable and logs every
        reply so the format can be worked out.
        """
        replies = []
        for accept in self._PUBLIC_FILES_ACCEPTS:
            try:
                content, reply = self._download_public_file(file_id, accept)
            except requests.RequestException as err:
                content, reply = None, str(err)
            if content is not None:
                _LOGGER.info(
                    "Camera images: using the public SmartThings files "
                    "endpoint (Accept: %s, %s)",
                    accept,
                    reply,
                )
                self._public_files_accept = accept
                return content
            replies.append(f"[Accept {accept}] {reply}")
        self.images_unavailable_reason = "no_samsung_account"
        _LOGGER.warning(
            "Camera images unavailable: the image server needs a Samsung ID "
            "(\"No samsung id available\") and the public files endpoint did "
            "not return an image. Replies: %s. The SmartThings connection "
            "itself is working. To get images, add a picture token (a "
            "SmartThings personal access token) or Samsung Account login to "
            "this integration.",
            " ; ".join(replies),
        )
        return None

    def _is_no_samsung_id_error(self, response: requests.Response) -> bool:
        """Return True if response body contains 'No samsung id' (400 auth error)."""
        try:
            return "No samsung id" in response.text
        except Exception:
            return False

    def _do_samsung_iot_refresh(self) -> None:
        """Refresh the Samsung IoT token in-place and persist the new refresh token."""
        from .auth import refresh_samsung_iot_token

        try:
            iot_creds = refresh_samsung_iot_token(
                self._samsung_iot_refresh_token,
                self._samsung_iot_auth_server,
            )
        except Exception as err:
            raise AuthenticationError(
                f"Samsung IoT token refresh failed: {err}"
            ) from err

        self.set_samsung_iot_token(
            iot_creds.access_token,
            refresh_token=iot_creds.refresh_token,
            auth_server=self._samsung_iot_auth_server,
            entry=self._entry,
        )

        if self._entry is not None:
            from .const import CONF_SAMSUNG_IOT_REFRESH_TOKEN
            new_data = {
                **self._entry.data,
                CONF_SAMSUNG_IOT_REFRESH_TOKEN: iot_creds.refresh_token,
            }
            self._hass.config_entries.async_update_entry(self._entry, data=new_data)

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
        if response.status_code == 400:
            try:
                body = response.json()
                err = body.get("error", {})
                if (
                    err.get("code") == "BadRequestError"
                    and "No samsung id" in err.get("message", "")
                ):
                    raise AuthenticationError(
                        "No samsung id available — switch to Standalone OAuth or add "
                        "Samsung Account credentials"
                    )
            except AuthenticationError:
                raise
            except Exception:
                pass
        if not response.ok:
            _LOGGER.warning(
                "SmartThings API request failed: HTTP %s - %s",
                response.status_code,
                response.text[:200],
            )

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        status = await self._hass.async_add_executor_job(self.get_all_device_status)
        # Keep the device list so `device_id` can be auto-detected from it.
        self.set_device_status(status)
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
        if self.images_unavailable_reason:
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
                url = (
                    f"https://client.smartthings.com/udo/file_links/"
                    f"{file_id}?cid={CID}&di={self.device_id}"
                )
                if self._public_files_accept:
                    content, reply = self._download_public_file(
                        file_id, self._public_files_accept
                    )
                    if content is None:
                        raise RuntimeError(f"public files endpoint: {reply}")
                    result[idx] = content
                    successes += 1
                    continue
                if self._samsung_iot_headers:
                    # Samsung Account IoT token (email/password login).
                    dl_headers = self._samsung_iot_headers
                elif self._image_headers:
                    # Picture token: a SmartThings PAT, which carries the
                    # Samsung ID the image server wants.
                    dl_headers = self._image_headers
                else:
                    dl_headers = self._headers
                r = requests.get(
                    url,
                    headers=dl_headers,
                    timeout=DEFAULT_TIMEOUT,
                )
                # Samsung IoT in-session refresh: when using IoT headers and the
                # server returns 401/403 or a 400 'No samsung id' error, attempt
                # a silent token refresh and retry once before surfacing as
                # ConfigEntryAuthFailed.
                if dl_headers is self._samsung_iot_headers:
                    auth_fail = r.status_code in (401, 403) or (
                        r.status_code == 400 and self._is_no_samsung_id_error(r)
                    )
                    if auth_fail:
                        if not self._samsung_iot_refresh_token:
                            raise AuthenticationError(
                                f"Samsung IoT authentication failed "
                                f"(HTTP {r.status_code}) — no refresh token available."
                            )
                        self._do_samsung_iot_refresh()
                        r = requests.get(
                            url,
                            headers=self._samsung_iot_headers,
                            timeout=DEFAULT_TIMEOUT,
                        )
                elif dl_headers is self._image_headers:
                    if r.status_code in (401, 403):
                        _LOGGER.error(
                            "Picture token rejected (HTTP %s) — it has probably "
                            "expired (SmartThings personal tokens last 24 hours). "
                            "Home Assistant will ask for a new one.",
                            r.status_code,
                        )
                        raise ImageTokenExpired(
                            f"Picture token rejected (HTTP {r.status_code}); "
                            "enter a new SmartThings personal access token."
                        )
                    if r.status_code == 400 and self._is_no_samsung_id_error(r):
                        self.images_unavailable_reason = "image_token_no_samsung_id"
                        _LOGGER.warning(
                            "Camera images unavailable: the picture token was "
                            "accepted but has no Samsung ID (\"No samsung id "
                            "available\"). Create the token at "
                            "https://account.smartthings.com/tokens while "
                            "signed in with your Samsung account."
                        )
                        return False
                elif (
                    dl_headers is self._headers
                    and r.status_code == 400
                    and self._is_no_samsung_id_error(r)
                ):
                    # The SmartThings token is fine; the image server just
                    # needs a Samsung identity. Reauth would not help, so try
                    # the public files endpoint once, else disable images.
                    content = self._probe_public_files_api(file_id)
                    if content is None:
                        return False
                    result[idx] = content
                    successes += 1
                    continue
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
                _LOGGER.debug(
                    "Camera details: picture %d is %s pixels (%d bytes)",
                    idx,
                    "x".join(map(str, jpeg_size(r.content) or ("?", "?"))),
                    len(r.content),
                )
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
        """List the devices in the account via the public SmartThings API.

        Uses ``api.smartthings.com/v1/devices`` rather than the private
        ``client.smartthings.com/devices/status`` endpoint: the private one
        rejects tokens that carry no Samsung Account identity (HA core
        SmartThings OAuth tokens, standalone OAuth tokens, most PATs) with
        400 "No samsung id available", which made setup fail.
        """
        r = requests.get(
            DEVICES_URL,
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

        # The public API pages large device lists; follow `_links.next`.
        pages = 1
        while isinstance(data, dict) and pages < 20:
            next_href = ((data.get("_links") or {}).get("next") or {}).get("href")
            if not next_href:
                break
            r = requests.get(
                next_href, headers=self._headers, timeout=DEFAULT_TIMEOUT
            )
            self._check_response(r)
            if not r.ok:
                break
            page = r.json()
            data = {
                **page,
                "items": list(data.get("items") or [])
                + list(page.get("items") or []),
            }
            pages += 1
        return data

    def log_camera_details(self) -> None:
        """Debug-log every camera-related capability in the full device status.

        The integration normally reads only the ``main`` component; this
        looks at all components once, to spot extra or larger pictures.
        """
        try:
            r = requests.get(
                f"https://api.smartthings.com/v1/devices/{self.device_id}/status",
                headers=self._headers,
                timeout=DEFAULT_TIMEOUT,
            )
            if not r.ok:
                _LOGGER.debug(
                    "Camera details: full status HTTP %s %s",
                    r.status_code,
                    r.text[:300],
                )
                return
            full = r.json()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Camera details: full status failed: %s", err)
            return
        components = (full.get("components") or {}) if isinstance(full, dict) else {}
        _LOGGER.debug(
            "Camera details: components and capabilities: %s",
            json.dumps({c: sorted(v) for c, v in components.items()
                        if isinstance(v, dict)})[:4000],
        )
        found = camera_related_status(full)
        if not found:
            _LOGGER.debug("Camera details: no camera-related capabilities found")
        for name, attrs in found.items():
            _LOGGER.debug(
                "Camera details: %s = %s",
                name,
                json.dumps(attrs, sort_keys=True)[:4000],
            )

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
            if not isinstance(element, dict):
                continue
            # Legacy private-endpoint format: one row per attribute.
            if (
                element.get("capabilityId") == VIEW_INSIDE_CAPABILITY
                and element.get("attributeName") == "contents"
            ):
                self._device_id = element["deviceId"]
                break
            # Public API format: one row per device, capabilities per component.
            for component in element.get("components") or []:
                caps = component.get("capabilities") or []
                if any(c.get("id") == VIEW_INSIDE_CAPABILITY for c in caps):
                    self._device_id = element.get("deviceId")
                    break
            if self._device_id:
                break
        if not self._device_id:
            _LOGGER.warning(
                "No device with the %s capability found in this SmartThings "
                "account — enter the fridge's Device ID manually",
                VIEW_INSIDE_CAPABILITY,
            )

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

