"""Minimal Home Assistant mocks for testing the custom component."""

import json
import logging
import os
import sys
import types
from unittest.mock import MagicMock, AsyncMock

import pytest

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# pytest CLI options for integration tests
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--smartthings-token",
        action="store",
        default=None,
        help="SmartThings personal access token (PAT) for integration tests",
    )
    parser.addoption(
        "--device-id",
        action="store",
        default=None,
        help="SmartThings device ID for integration tests",
    )
    parser.addoption(
        "--credentials",
        action="store",
        default=None,
        help=(
            "Path to .smartthings_credentials.json (from scripts/get_token.py). "
            "If provided, the refresh token is used to obtain a fresh access token "
            "automatically before tests run."
        ),
    )


@pytest.fixture
def smartthings_token(request):
    """Provide a valid SmartThings access token.

    Resolution order:
      1. --smartthings-token CLI flag (raw PAT)
      2. --credentials file (auto-refreshes via OAuth)
    """
    # Direct token takes precedence
    token = request.config.getoption("--smartthings-token")
    if token:
        return token

    # Fall back to credentials file with auto-refresh
    creds_path = request.config.getoption("--credentials")
    if creds_path:
        return _get_refreshed_token(creds_path)

    return None


@pytest.fixture
def device_id(request):
    return request.config.getoption("--device-id")


def _get_refreshed_token(creds_path: str) -> str:
    """Load credentials file, refresh the access token, save updated file."""
    # Resolve path relative to repo root
    if not os.path.isabs(creds_path):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        creds_path = os.path.join(repo_root, creds_path)

    with open(creds_path) as f:
        creds = json.load(f)

    # Late import so HA stubs are already in place
    from custom_components.samsung_familyhub_fridge.auth import SmartThingsOAuth

    oauth = SmartThingsOAuth(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )

    _logger.info("Refreshing SmartThings access token via OAuth...")
    new_creds = oauth.refresh(creds["refresh_token"])

    # Update the file so the next run uses the fresh refresh_token
    creds["access_token"] = new_creds.access_token
    creds["refresh_token"] = new_creds.refresh_token
    creds["expires_in"] = new_creds.expires_in
    with open(creds_path, "w") as f:
        json.dump(creds, f, indent=2)

    _logger.info("Token refreshed successfully (expires in %ds)", new_creds.expires_in)
    return new_creds.access_token


def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# --- Stub out homeassistant and its subpackages ---


class _HomeAssistant:
    """Minimal stub of homeassistant.core.HomeAssistant."""

    def __init__(self):
        self.async_add_executor_job = AsyncMock(side_effect=self._run_sync)

    async def _run_sync(self, func, *args):
        return func(*args)


class _ConfigEntry:
    """Minimal stub of homeassistant.config_entries.ConfigEntry."""

    def __init__(self, entry_id="test", data=None):
        self.entry_id = entry_id
        self.data = data or {}
        self._update_listeners = []

    def add_update_listener(self, listener):
        self._update_listeners.append(listener)
        return lambda: self._update_listeners.remove(listener)

    def async_on_unload(self, unsub):
        pass


class _ConfigEntryAuthFailed(Exception):
    pass


class _HomeAssistantError(Exception):
    pass


class _DataUpdateCoordinator:
    """Minimal stub of DataUpdateCoordinator."""

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class _SensorEntity:
    pass


class _Camera:
    def __init__(self):
        pass


class _UpdateEntity:
    pass


class _UpdateDeviceClass:
    pass


class _UpdateEntityFeature:
    pass


class _Platform:
    CAMERA = "camera"
    SENSOR = "sensor"


# Build fake module tree
ha = _make_module("homeassistant")
ha_core = _make_module("homeassistant.core", HomeAssistant=_HomeAssistant, callback=lambda f: f)
ha_config_entries = _make_module(
    "homeassistant.config_entries",
    ConfigEntry=_ConfigEntry,
    ConfigFlow=MagicMock,
)
ha_const = _make_module(
    "homeassistant.const",
    Platform=_Platform,
    HTTP_DIGEST_AUTHENTICATION="digest",
)
ha_exceptions = _make_module(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=_ConfigEntryAuthFailed,
    HomeAssistantError=_HomeAssistantError,
)
ha_data_entry_flow = _make_module(
    "homeassistant.data_entry_flow",
    FlowResult=dict,
)

# helpers tree
ha_helpers = _make_module("homeassistant.helpers")
ha_helpers_update_coordinator = _make_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinator,
    CoordinatorEntity=_CoordinatorEntity,
    UpdateFailed=Exception,
)
ha_helpers_entity_platform = _make_module(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=MagicMock,
)
ha_helpers_dispatcher = _make_module(
    "homeassistant.helpers.dispatcher",
    async_dispatcher_connect=MagicMock,
)
ha_helpers_typing = _make_module(
    "homeassistant.helpers.typing",
    ConfigType=dict,
    DiscoveryInfoType=dict,
)

# components tree
ha_components = _make_module("homeassistant.components")
ha_components_camera = _make_module(
    "homeassistant.components.camera",
    PLATFORM_SCHEMA=MagicMock,
    Camera=_Camera,
)
ha_components_local_file = _make_module("homeassistant.components.local_file")
ha_components_local_file_camera = _make_module(
    "homeassistant.components.local_file.camera",
    LocalFile=MagicMock,
)
ha_components_sensor = _make_module(
    "homeassistant.components.sensor",
    PLATFORM_SCHEMA=MagicMock,
    SensorEntity=_SensorEntity,
)
ha_components_update = _make_module(
    "homeassistant.components.update",
    UpdateDeviceClass=_UpdateDeviceClass,
    UpdateEntity=_UpdateEntity,
    UpdateEntityFeature=_UpdateEntityFeature,
)

# Register all in sys.modules
_modules = {
    "homeassistant": ha,
    "homeassistant.core": ha_core,
    "homeassistant.config_entries": ha_config_entries,
    "homeassistant.const": ha_const,
    "homeassistant.exceptions": ha_exceptions,
    "homeassistant.data_entry_flow": ha_data_entry_flow,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.update_coordinator": ha_helpers_update_coordinator,
    "homeassistant.helpers.entity_platform": ha_helpers_entity_platform,
    "homeassistant.helpers.dispatcher": ha_helpers_dispatcher,
    "homeassistant.helpers.typing": ha_helpers_typing,
    "homeassistant.components": ha_components,
    "homeassistant.components.camera": ha_components_camera,
    "homeassistant.components.local_file": ha_components_local_file,
    "homeassistant.components.local_file.camera": ha_components_local_file_camera,
    "homeassistant.components.sensor": ha_components_sensor,
    "homeassistant.components.update": ha_components_update,
}

for name, mod in _modules.items():
    sys.modules[name] = mod

# Also add voluptuous stub if missing
try:
    import voluptuous  # noqa: F401
except ImportError:
    vol = _make_module("voluptuous")
    vol.Schema = lambda *a, **kw: MagicMock()
    vol.Required = lambda *a, **kw: a[0] if a else "required"
    vol.Optional = lambda *a, **kw: a[0] if a else "optional"
    sys.modules["voluptuous"] = vol
