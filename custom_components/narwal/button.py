"""Button entities for one-shot Narwal actions (Pet Finding, end remote control)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NarwalConfigEntry
from .robot_settings_entities import settings_buttons


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NarwalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Narwal action buttons."""
    settings = getattr(entry.runtime_data, "robot_settings", None)
    if settings is not None:
        async_add_entities(settings_buttons(settings))
