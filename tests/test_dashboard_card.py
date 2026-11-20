"""The integration serves and registers the dashboard card."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _HomeAssistant


@pytest.mark.asyncio
async def test_setup_serves_card_and_adds_it_to_frontend():
    from custom_components.samsung_familyhub_fridge import CARD_URL, async_setup

    hass = _HomeAssistant()
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    with patch(
        "homeassistant.components.frontend.add_extra_js_url"
    ) as add_js:
        assert await async_setup(hass, {}) is True
    (paths,), _ = hass.http.async_register_static_paths.call_args
    url, path, cache = paths[0]
    assert url == CARD_URL
    assert path.endswith("frontend/familyhub-fridge-card.js")
    assert cache is False
    js_url = add_js.call_args[0][1]
    assert js_url.startswith(CARD_URL + "?v=")


@pytest.mark.asyncio
async def test_card_registration_failure_does_not_break_setup():
    from custom_components.samsung_familyhub_fridge import async_setup

    hass = _HomeAssistant()
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("x"))
    assert await async_setup(hass, {}) is True


@pytest.mark.asyncio
async def test_setup_without_http_skips_card():
    from custom_components.samsung_familyhub_fridge import async_setup

    assert await async_setup(_HomeAssistant(), {}) is True
