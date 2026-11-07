"""Robot settings (config/get + config/set), voice language and battery health.

Settings change rarely and only through the app or Home Assistant, so they are
polled on a slow interval and refreshed right after every write. A failed read
keeps the last values instead of making entities unavailable mid-session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .narwal_client.extended import FreoStrategy, decode_map_objects

if TYPE_CHECKING:
    from .coordinator import NarwalCoordinator

_LOGGER = logging.getLogger(__name__)

SETTINGS_POLL_INTERVAL = timedelta(minutes=10)
SERVICE_RESULT_SUCCESS = 1


@dataclass
class RobotSettingsData:
    """Last known robot settings."""

    config: dict[str, Any] = field(default_factory=dict)
    freo: dict[FreoStrategy, int | None] = field(default_factory=dict)
    language: int | None = None
    supported_languages: list[int] = field(default_factory=list)
    battery: dict[str, Any] = field(default_factory=dict)
    map_objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    maps: list[dict[str, Any]] = field(default_factory=list)
    continue_task: dict[str, Any] = field(default_factory=dict)
    firmware_status: int | None = None


class NarwalRobotSettings(DataUpdateCoordinator[RobotSettingsData]):
    """Polls robot settings and writes them back one field at a time."""

    def __init__(self, hass: HomeAssistant, coordinator: NarwalCoordinator) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=coordinator.config_entry,
            name=f"{coordinator.config_entry.title} settings",
            update_interval=SETTINGS_POLL_INTERVAL,
        )
        self.robot = coordinator
        self._last = RobotSettingsData()

    @property
    def client(self):
        """The shared NarwalClient."""
        return self.robot.client

    async def _async_update_data(self) -> RobotSettingsData:
        client = self.client
        if not client.connected:
            if self._last.config:
                return self._last
            raise UpdateFailed("Narwal not connected")
        data = RobotSettingsData(
            supported_languages=self._last.supported_languages,
        )
        try:
            config = await client.get_config()
            data.freo = config.pop("freo_preset", {})
            data.config = config
            data.language = await client.get_language()
            if not data.supported_languages:
                data.supported_languages = await client.get_supported_languages()
            data.battery = await client.get_battery_info()
            data.map_objects = decode_map_objects(await client.get_map_raw())
            data.maps = await client.list_maps()
            data.continue_task = await client.get_continue_task()
            data.firmware_status = await client.check_firmware()
        except Exception as err:  # noqa: BLE001 - keep last values on any read error
            if self._last.config:
                _LOGGER.debug("Narwal settings refresh failed, keeping last values: %s", err)
                return self._last
            raise UpdateFailed(f"Narwal settings read failed: {err}") from err
        self._last = data
        return data

    async def _ensure_awake(self) -> None:
        if not self.client.robot_awake:
            await self.client.wake(timeout=10.0)

    @staticmethod
    def _check(response: Any, what: str) -> None:
        result = response.data.get("1") if hasattr(response, "data") else None
        try:
            code = int(result)
        except (TypeError, ValueError):
            code = None
        if code is not None and code != SERVICE_RESULT_SUCCESS:
            raise HomeAssistantError(f"Narwal rejected {what} (result {code})")

    async def async_set(self, name: str, value: int | bool) -> None:
        """Write one setting and refresh."""
        await self._ensure_awake()
        response = await self.client.set_config(name, value)
        self._check(response, name)
        if self.data:
            self.data.config[name] = value
            self.async_set_updated_data(self.data)
        await self.async_request_refresh()

    async def async_set_freo(self, strategy: FreoStrategy, value: int | None) -> None:
        """Write one Freo Mind preset strategy (None = Smart)."""
        await self._ensure_awake()
        response = await self.client.set_freo_strategy(strategy, value)
        self._check(response, f"Freo {strategy.name.lower()}")
        if self.data:
            self.data.freo[strategy] = value
            self.async_set_updated_data(self.data)
        await self.async_request_refresh()

    async def async_call(self, what: str, call: Any) -> Any:
        """Run a client coroutine factory after waking the robot, then refresh."""
        await self._ensure_awake()
        result = await call()
        if isinstance(result, dict) and "success" in result and not result["success"]:
            raise HomeAssistantError(f"Narwal rejected {what}: {result}")
        if hasattr(result, "data"):
            self._check(result, what)
        # Immediate refresh (not the debounced request) so the entity shows the new value
        await self.async_refresh()
        return result

    @property
    def map_id(self) -> int:
        """Active map id."""
        map_data = self.client.state.map_data
        if map_data and map_data.map_id:
            return map_data.map_id
        maps = self.data.maps if self.data else []
        main = next((m for m in maps if m.get("is_main")), None)
        if main and main.get("map_id"):
            return int(main["map_id"])
        raise HomeAssistantError("Narwal map not loaded yet")

    async def async_set_language(self, language: int) -> None:
        """Switch the voice language (verified by reading back)."""
        await self._ensure_awake()
        if not await self.client.set_language(language):
            raise HomeAssistantError("Narwal did not switch language")
        if self.data:
            self.data.language = language
            self.async_set_updated_data(self.data)
