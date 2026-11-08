"""Serve and auto-register the Narwal map card (no manual Lovelace resource needed)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = "/narwal_static"
CARD_FILE = "narwal-map-card.js"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose frontend/ and load the card on every dashboard."""
    if hass.data.get(_REGISTERED):
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    from .websocket_api import async_register_websocket_api

    folder = Path(__file__).parent / "frontend"
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
    stamp = int((folder / CARD_FILE).stat().st_mtime)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(folder), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}/{CARD_FILE}?v={manifest.get('version', '0')}-{stamp}")
    async_register_websocket_api(hass)
    hass.data[_REGISTERED] = True
    _LOGGER.debug("Narwal map card registered at %s/%s", CARD_URL, CARD_FILE)
