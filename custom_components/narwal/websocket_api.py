"""WebSocket API for the Narwal map card: map geometry, live pose and remote control.

Coordinates are absolute map grid cells (the same frame as the clean_zone,
add_no_go_zone and add_carpet services). Decimetres = cells * resolution / 100.
"""

from __future__ import annotations

import base64
import math
import time
import zlib
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .narwal_client.extended import ManualControlMode, decode_map_objects
from .narwal_client.pb import all_values, as_float, as_signed, first, read_varint

MAP_CACHE_SECONDS = 20
PATH_MAX_POINTS = 6000
PATH_MIN_CELL_DELTA = 0.5
CELL_UNKNOWN = 0
CELL_WALL = 250
CELL_FLOOR = 251


def _coordinator(hass: HomeAssistant, entity_id: str | None):
    """Coordinator for a Narwal vacuum entity (or the only one loaded)."""
    loaded = list(hass.data.get(DOMAIN, {}).values())
    if entity_id:
        entry = er.async_get(hass).async_get(entity_id)
        if entry is not None:
            for coordinator in loaded:
                if coordinator.config_entry.entry_id == entry.config_entry_id:
                    return coordinator
    return loaded[0] if len(loaded) == 1 else None


def _room_names(hass: HomeAssistant, entity_id: str | None) -> dict[int, str]:
    """Room id -> HA area name from the vacuum's segment-to-area mapping."""
    if not entity_id:
        return {}
    entry = er.async_get(hass).async_get(entity_id)
    mapping = ((entry.options or {}).get("vacuum") or {}).get("area_mapping") if entry else None
    if not mapping:
        return {}
    from homeassistant.helpers import area_registry as ar

    areas = ar.async_get(hass)
    names: dict[int, str] = {}
    for area_id, segments in mapping.items():
        area = areas.async_get_area(area_id)
        for segment in segments or []:
            try:
                names[int(segment)] = area.name if area else area_id
            except (TypeError, ValueError):
                continue
    return names


def _pose(message: bytes | None, resolution: int) -> dict[str, float] | None:
    """PoseData (float32 decimetres) -> absolute cells + heading."""
    if not isinstance(message, bytes) or not resolution:
        return None
    location = first(message, 1)
    if not isinstance(location, bytes):
        return None
    x, y = as_float(first(location, 1)), as_float(first(location, 2))
    if x is None or y is None:
        return None
    theta = as_float(first(message, 2)) or 0.0
    factor = 100 / resolution
    return {"x": x * factor, "y": y * factor, "theta": theta}


def build_map_payload(map_raw: bytes, names: dict[int, str]) -> dict[str, Any]:
    """Compact map description for the card."""
    resolution = int(first(map_raw, 3) or 0)
    width = int(first(map_raw, 4) or 0)
    height = int(first(map_raw, 5) or 0)
    border = first(map_raw, 6)
    border = border if isinstance(border, bytes) else b""
    bottom = as_signed(first(border, 1)) or 0
    left = as_signed(first(border, 3)) or 0

    cells = bytearray(width * height)
    compressed = first(map_raw, 17)
    if isinstance(compressed, bytes) and width and height:
        grid = zlib.decompress(compressed, 47)
        packed = first(grid, 1)
        data = packed if isinstance(packed, bytes) else b""
        pos = index = 0
        while pos < len(data) and index < len(cells):
            value, pos = read_varint(data, pos)
            room = value >> 8
            flags = value & 0xFF
            if room:
                cells[index] = min(room, 249)
            elif flags == 40:
                cells[index] = CELL_WALL
            elif flags:
                cells[index] = CELL_FLOOR
            index += 1

    rooms = []
    for value in all_values(map_raw, 12):
        if not isinstance(value, bytes):
            continue
        room_id = int(first(value, 1) or 0)
        raw_name = first(value, 3)
        robot_name = raw_name.decode(errors="replace") if isinstance(raw_name, bytes) else ""
        rooms.append(
            {
                "id": room_id,
                "name": names.get(room_id) or robot_name or f"Room {room_id}",
                "robot_name": robot_name,
            }
        )
    rooms.sort(key=lambda r: r["id"])
    objects = decode_map_objects(map_raw)
    return {
        "map_id": int(first(map_raw, 1) or 0),
        "resolution": resolution,
        "width": width,
        "height": height,
        "left": left,
        "bottom": bottom,
        "cells": base64.b64encode(bytes(cells)).decode(),
        "rooms": rooms,
        "dock": _pose(first(map_raw, 8), resolution),
        "robot": _pose(first(map_raw, 24), resolution),
        "no_go_zones": objects["no_go_zones"],
        "carpets": objects["carpets"],
        "thresholds": objects["thresholds"],
        "rotation": int(first(map_raw, 25) or 0),
    }


def _live_pose(coordinator) -> dict[str, Any]:
    """Robot pose from the display_map broadcast (no robot call)."""
    state = coordinator.client.state
    display = getattr(state, "map_display_data", None)
    map_data = getattr(state, "map_data", None)
    resolution = getattr(map_data, "resolution", 0) or 0
    cache = getattr(coordinator, "_map_card_cache", None)
    if not resolution and cache:
        resolution = cache[1].get("resolution", 0)
    pose = None
    if resolution and display is not None and (display.robot_x or display.robot_y):
        # display_map carries float32 decimetres, like get_map's robotPose
        factor = 100 / resolution
        pose = {
            "x": float(display.robot_x) * factor,
            "y": float(display.robot_y) * factor,
            "heading_deg": float(getattr(display, "robot_heading", 0.0) or 0.0),
        }
    active = any(getattr(state, flag, False) for flag in ("is_cleaning", "is_paused", "is_returning"))
    signature = list(getattr(display, "trajectory_signature", ()) or ()) if active else []
    return {
        "robot": pose,
        "path_signature": signature or None,
        "status": getattr(getattr(state, "working_status", None), "name", None),
        "docked": bool(getattr(state, "is_docked", False)),
        "battery": getattr(state, "battery_level", None),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "narwal/map",
        vol.Optional("entity_id"): str,
        vol.Optional("refresh"): bool,
    }
)
@websocket_api.async_response
async def ws_map(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the map geometry (cached for a few seconds)."""
    coordinator = _coordinator(hass, msg.get("entity_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Narwal vacuum found")
        return
    cache = getattr(coordinator, "_map_card_cache", None)
    now = time.monotonic()
    if not msg.get("refresh") and cache and now - cache[0] < MAP_CACHE_SECONDS:
        connection.send_result(msg["id"], cache[1])
        return
    try:
        client = coordinator.client
        if not client.robot_awake:
            await client.wake(timeout=10.0)
        map_raw = await client.get_map_raw()
    except Exception as err:  # noqa: BLE001 - report any robot error to the card
        connection.send_error(msg["id"], "robot_error", str(err))
        return
    payload = build_map_payload(map_raw, _room_names(hass, msg.get("entity_id")))
    coordinator._map_card_cache = (now, payload)
    connection.send_result(msg["id"], payload)


def build_path(display: Any, resolution: int) -> list[list[float] | None]:
    """Native cleaning route in map cells; None marks a gap between segments."""
    if display is None or not resolution:
        return []
    factor = 100 / resolution
    path: list[list[float] | None] = []
    last: tuple[float, float] | None = None
    pending_break = False
    for raw_x, raw_y in display.trajectory_render_points():
        if not (math.isfinite(raw_x) and math.isfinite(raw_y)):
            pending_break = last is not None
            continue
        x, y = raw_x * factor, raw_y * factor
        if not pending_break and last and math.hypot(x - last[0], y - last[1]) < PATH_MIN_CELL_DELTA:
            continue
        if pending_break:
            path.append(None)
            pending_break = False
        path.append([round(x, 1), round(y, 1)])
        last = (x, y)
    if len(path) > PATH_MAX_POINTS:
        step = len(path) / PATH_MAX_POINTS
        keep = {int(i * step) for i in range(PATH_MAX_POINTS)} | {len(path) - 1}
        path = [p for i, p in enumerate(path) if i in keep or p is None]
    return path


@websocket_api.websocket_command(
    {vol.Required("type"): "narwal/path", vol.Optional("entity_id"): str}
)
@websocket_api.async_response
async def ws_path(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Return the current cleaning route (only polled when its signature changes)."""
    coordinator = _coordinator(hass, msg.get("entity_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Narwal vacuum found")
        return
    state = coordinator.client.state
    resolution = getattr(getattr(state, "map_data", None), "resolution", 0) or 0
    cache = getattr(coordinator, "_map_card_cache", None)
    if not resolution and cache:
        resolution = cache[1].get("resolution", 0)
    display = getattr(state, "map_display_data", None)
    path = await hass.async_add_executor_job(build_path, display, resolution)
    connection.send_result(msg["id"], {"path": path})


@websocket_api.websocket_command(
    {vol.Required("type"): "narwal/pose", vol.Optional("entity_id"): str}
)
@callback
def ws_pose(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Live robot pose and status."""
    coordinator = _coordinator(hass, msg.get("entity_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Narwal vacuum found")
        return
    connection.send_result(msg["id"], _live_pose(coordinator))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "narwal/remote",
        vol.Optional("entity_id"): str,
        vol.Optional("mode"): vol.In([m.name.lower() for m in ManualControlMode]),
        vol.Optional("linear"): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        vol.Optional("angular"): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
    }
)
@websocket_api.async_response
async def ws_remote(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Remote control: set the manual mode and/or send one velocity sample."""
    coordinator = _coordinator(hass, msg.get("entity_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "No Narwal vacuum found")
        return
    client = coordinator.client
    try:
        if "mode" in msg:
            if not client.robot_awake:
                await client.wake(timeout=10.0)
            await client.set_manual_control(ManualControlMode[msg["mode"].upper()])
        if "linear" in msg or "angular" in msg:
            await client.send_velocity(msg.get("linear", 0), msg.get("angular", 0))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "robot_error", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the card's WebSocket commands."""
    websocket_api.async_register_command(hass, ws_map)
    websocket_api.async_register_command(hass, ws_pose)
    websocket_api.async_register_command(hass, ws_path)
    websocket_api.async_register_command(hass, ws_remote)
