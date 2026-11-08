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
SERVICE_SET_ROOM_ORDER = "set_room_order"
SERVICE_ADD_CARPET = "add_carpet"
SERVICE_SET_CARPET_MODE = "set_carpet_mode"
SERVICE_DELETE_CARPET = "delete_carpet"
SERVICE_ADD_THRESHOLD = "add_threshold"
SERVICE_DELETE_THRESHOLD = "delete_threshold"
SERVICE_UPDATE_FURNITURE = "update_furniture"
SERVICE_DELETE_FURNITURE = "delete_furniture"
SERVICE_ROTATE_MAP = "rotate_map"
SERVICE_SPLIT_ROOM = "split_room"
SERVICE_MERGE_ROOMS = "merge_rooms"
SERVICE_SWITCH_MAP = "switch_map"
SERVICE_DELETE_MAP = "delete_map"
SERVICE_START_MAPPING = "start_mapping"
SERVICE_CONTINUE_TASK = "continue_task"

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
RECT = vol.ExactSequence([vol.Coerce(int), vol.Coerce(int), vol.Coerce(int), vol.Coerce(int)])

CLEAN_ZONE_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Optional("x1"): vol.Coerce(int),
        vol.Optional("y1"): vol.Coerce(int),
        vol.Optional("x2"): vol.Coerce(int),
        vol.Optional("y2"): vol.Coerce(int),
        # several rectangles [[x1, y1, x2, y2], ...], cleaned in order
        vol.Optional("zones"): vol.All(cv.ensure_list, [RECT]),
        vol.Optional("mode", default="vacuum_and_mop"): vol.In(MODES),
        vol.Optional("suction", default="standard"): vol.In(FANS),
        vol.Optional("water", default="normal"): vol.In(WATERS),
        vol.Optional("passes", default=1): vol.All(vol.Coerce(int), vol.Range(1, 3)),
        vol.Optional("freo", default=False): cv.boolean,
    }
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
CARPET_MODES = {"cross": 1, "clean": 2, "vacuum_and_mop": 3, "avoid": 4}
ID = vol.All(vol.Coerce(int), vol.Range(0, 100000))
SET_ROOM_ORDER_SCHEMA = vol.Schema({**TARGET, vol.Required("order"): cv.ensure_list})
ADD_CARPET_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("polygon"): vol.All(cv.ensure_list, vol.Length(min=3), [POINT]),
        vol.Optional("mode", default="clean"): vol.In(CARPET_MODES),
        vol.Optional("deep_clean", default=False): cv.boolean,
    }
)
SET_CARPET_MODE_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("carpet_id"): ID, vol.Required("mode"): vol.In(CARPET_MODES)}
)
DELETE_CARPET_SCHEMA = vol.Schema({**TARGET, vol.Required("carpet_id"): ID})
ADD_THRESHOLD_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("start"): POINT,
        vol.Required("end"): POINT,
        vol.Optional("height", default=20): vol.All(vol.Coerce(int), vol.Range(1, 60)),
    }
)
DELETE_THRESHOLD_SCHEMA = vol.Schema({**TARGET, vol.Required("threshold_id"): ID})
UPDATE_FURNITURE_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("furniture_id"): ID,
        vol.Optional("x"): vol.Coerce(float),
        vol.Optional("y"): vol.Coerce(float),
        vol.Optional("angle"): vol.Coerce(float),
    }
)
DELETE_FURNITURE_SCHEMA = vol.Schema({**TARGET, vol.Required("furniture_id"): ID})
ROTATE_MAP_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("angle"): vol.All(vol.Coerce(int), vol.In([0, 90, 180, 270]))}
)
SPLIT_ROOM_SCHEMA = vol.Schema(
    {
        **TARGET,
        vol.Required("room"): cv.string,
        vol.Required("start"): POINT,
        vol.Required("end"): POINT,
    }
)
MERGE_ROOMS_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("room_a"): cv.string, vol.Required("room_b"): cv.string}
)
MAP_ID_SCHEMA = vol.Schema({**TARGET, vol.Required("map_id"): ID})
DELETE_MAP_SCHEMA = vol.Schema(
    {**TARGET, vol.Required("map_id"): ID, vol.Required("confirm"): vol.In([True, "delete"])}
)
START_MAPPING_SCHEMA = vol.Schema(
    {**TARGET, vol.Optional("rebuild_current", default=False): cv.boolean}
)


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

    async def clean_zone(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        rects = [tuple(r) for r in call.data.get("zones") or []]
        if all(k in call.data for k in ("x1", "y1", "x2", "y2")):
            rects.insert(0, (call.data["x1"], call.data["y1"], call.data["x2"], call.data["y2"]))
        if not rects:
            raise HomeAssistantError("Give x1/y1/x2/y2 or a list of zones")
        if len(rects) > 10:
            raise HomeAssistantError("At most 10 zones per clean")
        response = await client.clean_zone(
            _map_id(coordinator),
            *rects[0],
            extra_zones=rects[1:],
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

    async def _one_room(coordinator, raw: str) -> int:
        rooms = await room_ids(coordinator, [raw])
        if len(rooms) != 1:
            raise HomeAssistantError(f"Select exactly one room (got {raw!r})")
        return rooms[0]

    async def _refresh(coordinator) -> None:
        settings = getattr(coordinator, "robot_settings", None)
        if settings is not None:
            await settings.async_request_refresh()
        await coordinator.async_request_refresh()

    async def set_room_order(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        rooms = await room_ids(coordinator, call.data["order"])
        if not rooms:
            raise HomeAssistantError("At least one room must be given")
        response = await client.set_room_order(rooms, _map_id(coordinator))
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal rejected the room order ({response.data})")
        await _refresh(coordinator)

    async def add_carpet(call: ServiceCall) -> dict[str, Any]:
        coordinator, client = await _client(call)
        polygon = [(int(round(x)), int(round(y))) for x, y in call.data["polygon"]]
        result = await client.add_carpet(
            polygon, CARPET_MODES[call.data["mode"]], call.data["deep_clean"]
        )
        _check_map(result, "carpet")
        await _refresh(coordinator)
        return {"carpet_id": result.get("id")}

    async def set_carpet_mode(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        result = await client.set_carpet_option(
            call.data["carpet_id"], CARPET_MODES[call.data["mode"]]
        )
        _check_map(result, "carpet mode")
        await _refresh(coordinator)

    async def delete_carpet(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        _check_map(await client.delete_carpet(call.data["carpet_id"]), "carpet removal")
        await _refresh(coordinator)

    async def add_threshold(call: ServiceCall) -> dict[str, Any]:
        coordinator, client = await _client(call)
        result = await client.add_threshold(
            tuple(call.data["start"]), tuple(call.data["end"]), call.data["height"]
        )
        _check_map(result, "obstacle crossing line")
        await _refresh(coordinator)
        return {"threshold_id": result.get("id")}

    async def delete_threshold(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        _check_map(await client.delete_threshold(call.data["threshold_id"]), "line removal")
        await _refresh(coordinator)

    async def update_furniture(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        try:
            result = await client.update_furniture(
                call.data["furniture_id"],
                x=call.data.get("x"),
                y=call.data.get("y"),
                angle=call.data.get("angle"),
            )
        except KeyError as err:
            raise HomeAssistantError(f"No furniture with id {err}") from err
        _check_map(result, "furniture")
        await _refresh(coordinator)

    async def delete_furniture(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        try:
            result = await client.delete_furniture(call.data["furniture_id"])
        except KeyError as err:
            raise HomeAssistantError(f"No furniture with id {err}") from err
        _check_map(result, "furniture removal")
        await _refresh(coordinator)

    async def rotate_map(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        _check_map(await client.rotate_map(call.data["angle"]), "map rotation")
        await _refresh(coordinator)

    async def split_room(call: ServiceCall) -> dict[str, Any]:
        coordinator, client = await _client(call)
        room = await _one_room(coordinator, call.data["room"])
        result = await client.split_room(room, tuple(call.data["start"]), tuple(call.data["end"]))
        _check_map(result, "room split")
        await _refresh(coordinator)
        return {"new_room_id": result.get("new_room_id")}

    async def merge_rooms(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        room_a = await _one_room(coordinator, call.data["room_a"])
        room_b = await _one_room(coordinator, call.data["room_b"])
        _check_map(await client.merge_rooms(room_a, room_b), "room merge")
        await _refresh(coordinator)

    async def switch_map(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        response = await client.switch_map(call.data["map_id"])
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal could not switch map ({response.data})")
        await _refresh(coordinator)

    async def delete_map(call: ServiceCall) -> None:
        coordinator, client = await _client(call)
        response = await client.delete_map(call.data["map_id"])
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal could not delete map ({response.data})")
        await _refresh(coordinator)

    async def start_mapping(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        response = await client.start_mapping(call.data["rebuild_current"])
        if not _accepted(response):
            raise HomeAssistantError(f"Narwal could not start mapping ({response.data})")

    async def continue_task(call: ServiceCall) -> None:
        _coordinator, client = await _client(call)
        response = await client.continue_task()
        if not _accepted(response):
            raise HomeAssistantError(f"Nothing to continue ({response.data})")

    registrations = (
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
        (SERVICE_SET_ROOM_ORDER, set_room_order, SET_ROOM_ORDER_SCHEMA, None),
        (SERVICE_ADD_CARPET, add_carpet, ADD_CARPET_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_SET_CARPET_MODE, set_carpet_mode, SET_CARPET_MODE_SCHEMA, None),
        (SERVICE_DELETE_CARPET, delete_carpet, DELETE_CARPET_SCHEMA, None),
        (SERVICE_ADD_THRESHOLD, add_threshold, ADD_THRESHOLD_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_DELETE_THRESHOLD, delete_threshold, DELETE_THRESHOLD_SCHEMA, None),
        (SERVICE_UPDATE_FURNITURE, update_furniture, UPDATE_FURNITURE_SCHEMA, None),
        (SERVICE_DELETE_FURNITURE, delete_furniture, DELETE_FURNITURE_SCHEMA, None),
        (SERVICE_ROTATE_MAP, rotate_map, ROTATE_MAP_SCHEMA, None),
        (SERVICE_SPLIT_ROOM, split_room, SPLIT_ROOM_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_MERGE_ROOMS, merge_rooms, MERGE_ROOMS_SCHEMA, None),
        (SERVICE_SWITCH_MAP, switch_map, MAP_ID_SCHEMA, None),
        (SERVICE_DELETE_MAP, delete_map, DELETE_MAP_SCHEMA, None),
        (SERVICE_START_MAPPING, start_mapping, START_MAPPING_SCHEMA, None),
        (SERVICE_CONTINUE_TASK, continue_task, TARGET_ONLY_SCHEMA, None),
    )
    for name, handler, schema, supports_response in registrations:
        if hass.services.has_service(DOMAIN, name):
            continue
        kwargs = {"schema": schema}
        if supports_response is not None:
            kwargs["supports_response"] = supports_response
        hass.services.async_register(DOMAIN, name, handler, **kwargs)
