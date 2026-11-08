"""Tests for the map card's WebSocket API (map geometry, live pose, remote control)."""

from __future__ import annotations

import base64
import gzip
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import tests.ha_stubs

tests.ha_stubs.install()

from custom_components.narwal import websocket_api as ws  # noqa: E402
from custom_components.narwal.narwal_client import pb  # noqa: E402


def _map_raw() -> bytes:
    """A 3x2 map: row y=-1 has room 1 + wall, row y=0 has room 2 + unknown."""
    width, height, left, bottom = 3, 2, -1, -1
    values = [1 << 8 | 1, 2 << 8 | 1, 40, 2 << 8 | 3, 0, 0]  # index = (y - bottom) * w + (x - left)
    grid = pb.f_packed(1, values)
    border = pb.f_int(1, bottom) + pb.f_int(3, left)
    dock = pb.f_msg(1, pb.point(-1.0, 0.0)) + pb.f_float(2, 0.0)
    return (
        pb.f_int(1, 7)
        + pb.f_int(3, 60)
        + pb.f_int(4, width)
        + pb.f_int(5, height)
        + pb.f_msg(6, border)
        + pb.f_msg(8, dock)
        + pb.f_msg(12, pb.f_int(1, 1) + pb.f_bytes(3, "Study1"))
        + pb.f_msg(12, pb.f_int(1, 2))
        + pb.f_msg(17, gzip.compress(grid))
        + pb.f_int(25, 180)
    )


def test_build_map_payload_cells_rooms_and_pose() -> None:
    payload = ws.build_map_payload(_map_raw(), {2: "Kitchen"})
    assert (payload["map_id"], payload["width"], payload["height"]) == (7, 3, 2)
    assert (payload["left"], payload["bottom"], payload["rotation"]) == (-1, -1, 180)
    cells = list(base64.b64decode(payload["cells"]))
    assert cells == [1, 2, ws.CELL_WALL, 2, 0, 0]
    names = {r["id"]: r["name"] for r in payload["rooms"]}
    assert names == {1: "Study1", 2: "Kitchen"}  # HA area name wins, robot name is the fallback
    # poses are already in cells
    assert round(payload["dock"]["x"], 3) == -1.0


def test_live_pose_is_already_in_cells() -> None:
    state = SimpleNamespace(
        map_display_data=SimpleNamespace(robot_x=-14.0, robot_y=-1.0, robot_heading=90.0),
        working_status=SimpleNamespace(name="CLEANING"),
        is_docked=False,
        battery_level=87,
    )
    coordinator = SimpleNamespace(client=SimpleNamespace(state=state))
    pose = ws._live_pose(coordinator)
    assert round(pose["robot"]["x"], 1) == -14.0 and round(pose["robot"]["y"], 1) == -1.0
    assert pose["robot"]["heading_deg"] == 90.0
    assert pose["battery"] == 87 and pose["status"] == "CLEANING"


def test_live_pose_defers_to_the_map_when_docked() -> None:
    state = SimpleNamespace(
        map_display_data=SimpleNamespace(robot_x=-1.8, robot_y=-0.5, robot_heading=173.0),
        working_status=SimpleNamespace(name="CHARGED"),
        is_docked=True,
        battery_level=72,
    )
    pose = ws._live_pose(SimpleNamespace(client=SimpleNamespace(state=state)))
    assert pose["robot"] is None and pose["docked"] is True


def test_build_path_keeps_gaps_and_drops_tiny_moves() -> None:
    nan = float("nan")
    display = SimpleNamespace(
        trajectory_render_points=lambda: [(1.0, 1.0), (1.1, 1.0), (2.0, 1.0), (nan, nan), (10.0, 10.0)]
    )
    assert ws.build_path(display) == [[1.0, 1.0], [2.0, 1.0], None, [10.0, 10.0]]
    assert ws.build_path(None) == []


def test_remote_command_sets_mode_and_velocity() -> None:
    import asyncio

    client = MagicMock()
    client.robot_awake = True
    client.set_manual_control = AsyncMock()
    client.send_velocity = AsyncMock()
    coordinator = SimpleNamespace(client=client, config_entry=SimpleNamespace(entry_id="e1"))
    hass = SimpleNamespace(data={"narwal": {"e1": coordinator}})
    connection = MagicMock()
    asyncio.run(
        ws.ws_remote(
            hass, connection, {"id": 1, "type": "narwal/remote", "mode": "joystick", "linear": 40}
        )
    )
    client.set_manual_control.assert_awaited_once()
    client.send_velocity.assert_awaited_once_with(40, 0)
    connection.send_result.assert_called_once_with(1, {"ok": True})


def test_card_file_is_shipped() -> None:
    import pathlib

    card = pathlib.Path(ws.__file__).parent / "frontend" / "narwal-map-card.js"
    text = card.read_text(encoding="utf-8")
    assert 'customElements.define("narwal-map-card"' in text
    assert "narwal/map" in text and "narwal/remote" in text
