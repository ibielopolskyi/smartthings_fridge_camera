"""Unit tests for the OAuth-reuse auth path.

Covers:
- FamilyHub.attach_oauth_session / async_ensure_fresh_token
- FamilyHub transparently picks up a rotated access_token from the session
- DataCoordinator._async_update_data calls async_ensure_fresh_token before
  each cycle (via the hub)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import requests_mock as rm

# conftest installs HA stubs before any component imports
from tests.conftest import (
    _HomeAssistant,
    _OAuth2SessionStub,
)

from custom_components.samsung_familyhub_fridge.api import (
    DataCoordinator,
    FamilyHub,
)


@pytest.fixture
def hass():
    return _HomeAssistant()


@pytest.fixture
def session():
    return _OAuth2SessionStub(hass=None, entry=None, impl=None)


@pytest.fixture
def hub_oauth(hass, session):
    # Initial token is passed at construction (what the plumbing does after
    # first async_ensure_token_valid in __init__.py), then the session is
    # attached so subsequent refreshes are automatic.
    hub = FamilyHub(hass, token="oauth-token-initial", device_id="device-123")
    hub.attach_oauth_session(session)
    return hub


# ---------------------------------------------------------------------------
# FamilyHub.async_ensure_fresh_token
# ---------------------------------------------------------------------------

async def test_async_ensure_fresh_token_noop_for_pat(hass):
    """PAT-mode hubs never call the OAuth session."""
    hub = FamilyHub(hass, token="pat-token", device_id="d")
    # No session attached → call should return quietly and leave token alone
    await hub.async_ensure_fresh_token()
    assert hub.token == "pat-token"


async def test_async_ensure_fresh_token_calls_session(hub_oauth, session):
    """OAuth-mode hubs defer refresh to the attached session each call."""
    assert session.ensure_calls == 0
    await hub_oauth.async_ensure_fresh_token()
    assert session.ensure_calls == 1
    await hub_oauth.async_ensure_fresh_token()
    assert session.ensure_calls == 2


async def test_async_ensure_fresh_token_rotates_bearer(hub_oauth, session):
    """When the session rotates the access_token, the bearer header updates."""
    assert hub_oauth.token == "oauth-token-initial"
    assert hub_oauth._headers["Authorization"] == "Bearer oauth-token-initial"

    # Arrange for the next ensure-call to return a fresh token
    session.queue_refresh("oauth-token-rotated")
    await hub_oauth.async_ensure_fresh_token()

    assert hub_oauth.token == "oauth-token-rotated"
    assert hub_oauth._headers["Authorization"] == "Bearer oauth-token-rotated"


async def test_async_ensure_fresh_token_idempotent_when_unchanged(hub_oauth, session):
    """If the session's token is unchanged, the header is not rewritten needlessly."""
    original_headers = hub_oauth._headers
    await hub_oauth.async_ensure_fresh_token()
    # Same object identity — update_token was never called
    assert hub_oauth._headers is original_headers


# ---------------------------------------------------------------------------
# DataCoordinator integration
# ---------------------------------------------------------------------------

async def test_coordinator_refreshes_token_before_each_poll(hub_oauth, session, hass):
    """Coordinator awaits the hub's fresh-token helper before any API call."""
    coordinator = DataCoordinator(hass, hub_oauth)

    # Make the underlying sync methods pure no-ops for this test
    hub_oauth.get_current_device_status = MagicMock(return_value={})
    hub_oauth.set_current_device_status = MagicMock()
    hub_oauth.extract_device_data = MagicMock()
    hub_oauth.get_file_ids = MagicMock(return_value=[])

    session.queue_refresh("rotated-1")
    await coordinator._async_update_data()
    assert session.ensure_calls == 1
    assert hub_oauth.token == "rotated-1"

    session.queue_refresh("rotated-2")
    await coordinator._async_update_data()
    assert session.ensure_calls == 2
    assert hub_oauth.token == "rotated-2"


async def test_coordinator_noop_ensure_for_pat(hass, requests_mock):
    """PAT-mode coordinator skips the OAuth refresh path entirely."""
    hub = FamilyHub(hass, token="pat-token", device_id="device-123")
    coordinator = DataCoordinator(hass, hub)

    hub.get_current_device_status = MagicMock(return_value={})
    hub.set_current_device_status = MagicMock()
    hub.extract_device_data = MagicMock()
    hub.get_file_ids = MagicMock(return_value=[])

    # Must complete without any OAuth session attached
    await coordinator._async_update_data()
    # Token is unchanged
    assert hub.token == "pat-token"
