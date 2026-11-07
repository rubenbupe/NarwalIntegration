"""Time entities for Narwal schedules (Do Not Disturb, off-peak charging)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NarwalConfigEntry
from .robot_settings_entities import robot_settings_for, settings_times


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NarwalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Narwal schedule time entities."""
    settings = robot_settings_for(entry.runtime_data)
    if settings is not None:
        async_add_entities(settings_times(settings))
