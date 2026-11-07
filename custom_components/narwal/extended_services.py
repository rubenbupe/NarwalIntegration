"""Services for the extended local commands: Freo, zones, map edits, movement, pets."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .narwal_client.extended import FloorTexture, ForbiddenType, ManualControlMode

_LOGGER = logging.getLogger(__name__)

SERVICE_CLEAN_ROOMS_FREO = "clean_rooms_freo"
SERVICE_CLEAN_ZONE = "clean_zone"
SERVICE_GO_TO_POINT = "go_to_point"
SERVICE_REMOTE_MOVE = "remote_move"
SERVICE_PLAY_PET_VOICE = "play_pet_voice"
SERVICE_GET_PET_VOICES = "get_pet_voices"
SERVICE_RESET_CONSUMABLES = "reset_consumables"
SERVICE_ADD_NO_GO_ZONE = "add_no_go_zone"
SERVICE_ADD_ROOM_BAN = "add_room_ban"
SERVICE_REMOVE_NO_GO_ZONE = "remove_no_go_zone"
SERVICE_UPDATE_ROOM = "update_room"
SERVICE_SET_MAP_NAME = "set_map_name"

TARGET = {
    vol.Optional("entity_id"): cv.comp_entity_ids,
    vol.Optional("device_id"): cv.ensure_list,
    vol.Optional("area_id"): cv.ensure_list,
}

MODES = {"vacuum": 2, "mop": 3, "vacuum_and_mop": 4, "vacuum_then_mop": 5}
FANS = {"quiet": 1, "standard": 2, "strong": 3, "super_powerful": 4, "max": 5}
WATERS = {"dry": 1, "normal": 2, "wet": 3}
ZONE_TYPES = {
    "no_go": ForbiddenType.SWEEP_MOP,
    "no_mop": ForbiddenType.MOP,
    "no_sweep": ForbiddenType.SWEEP,
}
FLOORS = {texture.name.lower(): texture for texture in FloorTexture}
MANUAL_MODES = {mode.name.lower(): mode for mode in ManualControlMode}

POINT = vol.All(vol.ExactSequence([vol.Coerce(float), vol.Coerce(float)]))

CLEAN_ZONE_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("x1"): vol.Coerce(int),
        vol.Required("y1"): vol.Coerce(int),
        vol.Required("x2"): vol.Coerce(int),
        vol.Required("y2"): vol.Coerce(int),
        vol.Optional("mode", default="vacuum_and_mop"): vol.In(MODES),
        vol.Optional("suction", default="standard"): vol.In(FANS),
        vol.Optional("water", default="normal"): vol.In(WATERS),
        vol.Optional("passes", default=1): vol.All(vol.Coerce(int), vol.Range(1, 3)),
        vol.Optional("freo", default=False): cv.boolean,
    }
)
CLEAN_ROOMS_FREO_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("rooms"): cv.ensure_list}
)
GO_TO_POINT_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("x"): vol.Coerce(float), vol.Required("y"): vol.Coerce(float)}
)
REMOTE_MOVE_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Optional("linear", default=0): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("angular", default=0): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("duration", default=1.0): vol.All(vol.Coerce(float), vol.Range(0.2, 10)),
        vol.Optional("mode", default="joystick"): vol.In(MANUAL_MODES),
        vol.Optional("end", default=False): cv.boolean,
    }
)
PLAY_PET_VOICE_SCHEMA = vol.Schema({**TARGET, vol.Required("path"): cv.string})
TARGET_ONLY_SCHEMA = vol.Schema(TARGET)
RESET_CONSUMABLES_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Optional("maintain", default=[]): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional("replace", default=[]): vol.All(cv.ensure_list, [vol.Coerce(int)]),
    }
)
ADD_NO_GO_ZONE_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("polygon"): vol.All(cv.ensure_list, vol.Length(min=3), [POINT]),
        vol.Required("zone_id"): vol.All(vol.Coerce(int), vol.Range(1, 999)),
        vol.Optional("type", default="no_go"): vol.In(ZONE_TYPES),
    }
)
ADD_ROOM_BAN_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("room"): cv.string,
        vol.Required("zone_id"): vol.All(vol.Coerce(int), vol.Range(1, 999)),
        vol.Optional("type", default="no_go"): vol.In(ZONE_TYPES),
    }
)
REMOVE_NO_GO_ZONE_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("zone_id"): vol.All(vol.Coerce(int), vol.Range(1, 999))}
)
UPDATE_ROOM_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("room"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("floor"): vol.In(FLOORS),
        vol.Optional("heavy_dirty"): cv.boolean,
    }
)
SET_MAP_NAME_SCHEMA = vol.Schema({**TARGET, vol.Required("name"): cv.string})


def _accepted(response: Any) -> bool:
    data = getattr(response, "data", {}) or {}
    try:
        return int(data.get("1", 0)) in (0, 1)
    except (TypeError, ValueError):
        return True


def async_register_extended_services(
    hass: HomeAssistant,
    resolve: Callable[[ServiceCall], Awaitable[Any]],
    room_ids: Callable[[Any, list[Any]], Awaitable[list[int]]],
) -> None:
    """Register the extended services.

    resolve(call) returns the single targeted NarwalCoordinator; room_ids maps
    room names, ids or areas to robot room ids (same rules as clean_rooms).
    """

    async def _client(call: ServiceCall):
        coordinator = await resolve(call)
        client = coordinator.client
        if not client.robot_awake:
            await client.wake(timeout=10.0)
        return coordinator, client

    def _map_id(coordinator) -> int:
        map_data = coordinator.client.state.map_data
        if not map_data or not map_data.map_id:
            raise HomeAssistantError("Narwal map not loaded yet")
        return map_data.map_id

    def _check_map(result: dict[str, Any], what: str) -> None:
        if not result.get("success"):
            raise HomeAssistantError(f"Narwal rejected {what}: {result}")

    async def clean_rooms_freo(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        rooms = await room_ids(coordinator, call.data["rooms"])
        if not rooms:
            raise HomeAssistantError("At least one room must be selected")
        response = await client.start_rooms(rooms, freo=True)
        if not response.accepted:
            raise HomeAssistantError(f"Narwal Freo clean failed ({response.result_code})")

    async def clean_zone(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        response = await client.clean_zone(
            _map_id(coordinator),
            call.data["x1"],
            call.data["y1"],
            call.data["x2"],
            call.data["y2"],
            mode=MODES[call.data["mode"]],
            fan=FANS[call.data["suction"]],
            water=WATERS[call.data["water"]],
            passes=call.data["passes"],
            freo=call.data["freo"],
        )
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal zone clean failed ({response.data})")

    async def go_to_point(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        response = await client.go_to_point(call.data["x"], call.data["y"], _map_id(coordinator))
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal go to point failed ({response.data})")

    async def remote_move(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        response = await client.set_manual_control(MANUAL_MODES[call.data["mode"]])
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal remote control refused ({response.data})")
        if call.data["linear"] or call.data["angular"]:
            await client.remote_move(
                call.data["linear"], call.data["angular"], call.data["duration"]
            )
        if call.data["end"]:
            await client.set_manual_control(ManualControlMode.OFF)

    async def play_pet_voice(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        try:
            response = await client.play_voice_file(call.data["path"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal could not play {call.data['path']}")

    async def get_pet_voices(call: ServiceCall) -> dict[str, Any]:
        _coordinator, client = await _client(call)
        voices = await client.get_pet_voices()
        return {
            "voices": [
                {"kind": v.kind, "name": v.name, "path": v.path, "image": v.image_url}
                for v in voices
            ]
        }

    async def reset_consumables(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        try:
            response = await client.reset_consumables(call.data["maintain"], call.data["replace"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal consumable reset failed ({response.data})")

    async def add_no_go_zone(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        polygon = [(float(x), float(y)) for x, y in call.data["polygon"]]
        result = await client.add_no_go_zone(
            polygon, call.data["zone_id"], ZONE_TYPES[call.data["type"]]
        )
        _check_map(result, "no-go zone")

    async def add_room_ban(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        rooms = await room_ids(coordinator, [call.data["room"]])
        if len(rooms) != 1:
            raise HomeAssistantError("Select exactly one room")
        result = await client.add_room_ban(
            rooms[0], call.data["zone_id"], ZONE_TYPES[call.data["type"]]
        )
        _check_map(result, "room ban")

    async def remove_no_go_zone(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        _check_map(await client.remove_no_go_zone(call.data["zone_id"]), "zone removal")

    async def update_room(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        rooms = await room_ids(coordinator, [call.data["room"]])
        if len(rooms) != 1:
            raise HomeAssistantError("Select exactly one room")
        floor = call.data.get("floor")
        result = await client.update_room(
            rooms[0],
            name=call.data.get("name"),
            floor=FLOORS[floor] if floor else None,
            heavy_dirty=call.data.get("heavy_dirty"),
        )
        _check_map(result, "room update")
        await coordinator.async_request_refresh()

    async def set_map_name(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        _check_map(await client.set_map_name(call.data["name"]), "map name")

    registrations = (
        (SERVICE_CLEAN_ROOMS_FREO, clean_rooms_freo, CLEAN_ROOMS_FREO_SCHEMA, None),
        (SERVICE_CLEAN_ZONE, clean_zone, CLEAN_ZONE_SCHEMA, None),
        (SERVICE_GO_TO_POINT, go_to_point, GO_TO_POINT_SCHEMA, None),
        (SERVICE_REMOTE_MOVE, remote_move, REMOTE_MOVE_SCHEMA, None),
        (SERVICE_PLAY_PET_VOICE, play_pet_voice, PLAY_PET_VOICE_SCHEMA, None),
        (SERVICE_GET_PET_VOICES, get_pet_voices, TARGET_ONLY_SCHEMA, SupportsResponse.ONLY),
        (SERVICE_RESET_CONSUMABLES, reset_consumables, RESET_CONSUMABLES_SCHEMA, None),
        (SERVICE_ADD_NO_GO_ZONE, add_no_go_zone, ADD_NO_GO_ZONE_SCHEMA, None),
        (SERVICE_ADD_ROOM_BAN, add_room_ban, ADD_ROOM_BAN_SCHEMA, None),
        (SERVICE_REMOVE_NO_GO_ZONE, remove_no_go_zone, REMOVE_NO_GO_ZONE_SCHEMA, None),
        (SERVICE_UPDATE_ROOM, update_room, UPDATE_ROOM_SCHEMA, None),
        (SERVICE_SET_MAP_NAME, set_map_name, SET_MAP_NAME_SCHEMA, None),
    )
    for name, handler, schema, supports_response in registrations:
        if hass.services.has_service(DOMAIN, name):
            continue
        kwargs = {"schema": schema}
        if supports_response is not None:
            kwargs["supports_response"] = supports_response
        hass.services.async_register(DOMAIN, name, handler, **kwargs)
