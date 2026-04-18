"""The Samsung FamilyHub Fridge integration.

Auth modes (data["auth_mode"]):

- "oauth"  : Reuse the HA core `smartthings` integration's OAuth2 credentials.
             Tokens refresh automatically via HA's OAuth2Session — no manual
             PAT rotation. Requires a working `smartthings` config entry
             referenced by `data["linked_smartthings_entry_id"]`.

- "pat"    : Legacy SmartThings Personal Access Token (raw string). Samsung
             deprecated indefinite PATs on 2024-12-30 — new PATs expire after
             24 hours. Retained for backwards compatibility only.

Config entries created before this integration version stored `{token, device_id}`
without an `auth_mode` key; they are migrated to `auth_mode: "pat"` on first
load via `async_migrate_entry`.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow

from .api import FamilyHub
from .const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_PAT,
    CONF_AUTH_MODE,
    CONF_DEVICE_ID,
    CONF_LINKED_SMARTTHINGS_ENTRY_ID,
    CONF_SAMSUNG_IOT_AUTH_SERVER,
    CONF_SAMSUNG_IOT_REFRESH_TOKEN,
    CONF_TOKEN,
    DOMAIN,
    SMARTTHINGS_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Samsung FamilyHub Fridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    auth_mode = entry.data.get(CONF_AUTH_MODE, AUTH_MODE_PAT)
    device_id = entry.data.get(CONF_DEVICE_ID)

    if auth_mode == AUTH_MODE_OAUTH:
        hub = await _build_oauth_hub(hass, entry, device_id)
    else:
        # Legacy PAT path — unchanged from v0.0.x.
        token = entry.data.get(CONF_TOKEN)
        if not token:
            raise ConfigEntryNotReady(
                "PAT-mode config entry has no token. Reconfigure the "
                "integration in Settings → Devices & Services."
            )
        hub = FamilyHub(hass, token=token, device_id=device_id)

    hass.data[DOMAIN][entry.entry_id] = entry
    hass.data[DOMAIN]["hub"] = hub

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_refresh(call: ServiceCall) -> None:
        """Manually trigger a fridge camera refresh."""
        _LOGGER.info("Manual refresh requested — sending update_camera command")
        await hub.async_ensure_fresh_token()
        await hass.async_add_executor_job(hub.update_camera)
        hub.should_update = False  # already sent the command
        _LOGGER.info("Manual refresh command sent successfully")

    hass.services.async_register(DOMAIN, "refresh", _handle_refresh)

    return True


async def _build_oauth_hub(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str | None
) -> FamilyHub:
    """Construct a FamilyHub that borrows the HA core smartthings OAuth session.

    Raises ConfigEntryNotReady if the linked smartthings entry is missing or
    not loaded yet — HA will retry the setup automatically.
    """
    linked_id = entry.data.get(CONF_LINKED_SMARTTHINGS_ENTRY_ID)
    if not linked_id:
        raise ConfigEntryNotReady(
            "OAuth-mode entry missing linked_smartthings_entry_id — "
            "reconfigure to re-link the HA core SmartThings integration."
        )

    smartthings_entry = hass.config_entries.async_get_entry(linked_id)
    if smartthings_entry is None or smartthings_entry.domain != SMARTTHINGS_DOMAIN:
        raise ConfigEntryNotReady(
            f"Linked SmartThings entry {linked_id} not found. "
            "Re-add the HA core SmartThings integration and reconfigure this one."
        )

    impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, smartthings_entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, smartthings_entry, impl)

    try:
        await session.async_ensure_token_valid()
    except Exception as err:  # pylint: disable=broad-except
        # OAuth2Session wraps errors in aiohttp/client exceptions. The SmartThings
        # entry itself will handle reauth — we just need to back off here.
        raise ConfigEntryNotReady(
            f"Failed to obtain a fresh SmartThings OAuth token: {err}"
        ) from err

    token = session.token["access_token"]
    hub = FamilyHub(hass, token=token, device_id=device_id)
    hub.attach_oauth_session(session)

    # Attach Samsung IoT token for client.smartthings.com image downloads.
    # This is a separate token from the SmartThings API OAuth — it carries
    # Samsung Account identity which the udo/file_links endpoint requires.
    iot_refresh = entry.data.get(CONF_SAMSUNG_IOT_REFRESH_TOKEN)
    iot_server = entry.data.get(
        CONF_SAMSUNG_IOT_AUTH_SERVER, "https://us-auth2.samsungosp.com"
    )
    if iot_refresh:
        try:
            from .auth import refresh_samsung_iot_token

            iot_creds = await hass.async_add_executor_job(
                refresh_samsung_iot_token, iot_refresh, iot_server
            )
            hub.set_samsung_iot_token(iot_creds.access_token)
            # Persist the new refresh token for next startup
            if iot_creds.refresh_token != iot_refresh:
                new_data = {
                    **entry.data,
                    CONF_SAMSUNG_IOT_REFRESH_TOKEN: iot_creds.refresh_token,
                }
                hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info("Samsung IoT token refreshed for image downloads")
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Could not refresh Samsung IoT token — image downloads "
                "will fail until resolved: %s",
                err,
            )

    return hub


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (e.g. after re-authentication)."""
    hub: FamilyHub = hass.data[DOMAIN]["hub"]
    auth_mode = entry.data.get(CONF_AUTH_MODE, AUTH_MODE_PAT)
    if auth_mode == AUTH_MODE_PAT:
        new_token = entry.data.get(CONF_TOKEN)
        if new_token:
            hub.update_token(new_token)
    # OAuth mode refreshes its token automatically — nothing to do here.


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop("hub", None)
        # Only deregister the service when no other entries remain
        if not [
            eid for eid in hass.data[DOMAIN] if eid != "hub"
        ]:
            hass.services.async_remove(DOMAIN, "refresh")

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry data to the current schema.

    v1  → {token, device_id}                     (raw PAT)
    v2  → adds auth_mode=(pat|oauth), optional linked_smartthings_entry_id
    """
    if entry.version == 1:
        new_data: dict[str, Any] = {**entry.data, CONF_AUTH_MODE: AUTH_MODE_PAT}
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info(
            "Migrated samsung_familyhub_fridge entry %s v1→v2 (auth_mode=pat). "
            "Reconfigure in UI to switch to OAuth.",
            entry.entry_id,
        )
    return True
