"""Tests for the extended local commands (narwal_client.extended / pb).

Payloads are compared with the frames that worked against a real Flow (AX12,
firmware v01.08.03.07). No Home Assistant needed.
"""

from __future__ import annotations

import struct

import pytest

from narwal_client import pb
from narwal_client.extended import (
    CONFIG_FIELDS,
    ExtendedCommandsMixin,
    FreoStrategy,
    decode_config,
    decode_freo_preset,
)
from narwal_client.models import CommandResponse


class FakeClient(ExtendedCommandsMixin):
    """Records commands and answers from a queue of raw payloads."""

    def __init__(self, answers: list[bytes] | None = None) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self.published: list[tuple[str, bytes]] = []
        self.answers = list(answers or [])

    async def send_command(self, topic, payload=b"", timeout=10.0):
        self.sent.append((topic, payload))
        raw = self.answers.pop(0) if self.answers else pb.f_int(1, 1)
        return CommandResponse(result_code=1, data={}, raw_payload=raw)

    async def _send_unawaited(self, topic, payload):
        self.published.append((topic, payload))


def test_varint_and_negative_ints() -> None:
    assert pb.varint(1) == b"\x01"
    assert pb.varint(300) == b"\xac\x02"
    assert len(pb.varint(-1)) == 10
    value, _ = pb.read_varint(pb.varint(-24), 0)
    assert pb.as_signed(value) == -24


def test_float_point_encoding() -> None:
    assert pb.point(10.0, -10.0) == b"\x0d" + struct.pack("<f", 10.0) + b"\x15" + struct.pack(
        "<f", -10.0
    )


def test_replace_field_keeps_other_fields_sorted() -> None:
    msg = pb.f_int(1, 9) + pb.f_bytes(3, "Kitchen") + pb.f_int(4, 2)
    out = pb.replace_field(msg, 4, pb.f_int(4, 1))
    assert pb.first(out, 4) == 1
    assert pb.first(out, 3) == b"Kitchen"
    assert pb.replace_field(out, 3, None) == pb.f_int(1, 9) + pb.f_int(4, 1)


def test_decode_config_uses_get_field_numbers() -> None:
    # Get numbers: volume 1, childLock 7, avoidMode 34, stationLight 51
    config = pb.f_int(1, 80) + pb.f_int(7, 1) + pb.f_int(34, 2) + pb.f_int(51, 2)
    values = decode_config(config)
    assert values["volume"] == 80
    assert values["child_lock"] is True
    assert values["obstacle_avoidance"] == 2
    assert values["station_light"] == 2
    assert values["pet_mode"] is False  # absent bool = false
    assert values["robot_default_mode"] is None  # absent enum


def test_set_config_sends_one_unwrapped_set_field() -> None:
    client = FakeClient()
    import asyncio

    asyncio.run(client.set_config("station_light", 1))
    # Set number for stationLightCtrlType is 49 (Get 51)
    assert client.sent == [("config/set", pb.f_int(49, 1))]
    with pytest.raises(ValueError):
        asyncio.run(client.set_config("station_light", 7))


def test_every_config_field_has_distinct_numbers() -> None:
    sets = [spec.set_field for spec in CONFIG_FIELDS.values()]
    gets = [spec.get_field for spec in CONFIG_FIELDS.values()]
    assert len(set(sets)) == len(sets)
    assert len(set(gets)) == len(gets)


def test_freo_preset_round_trip() -> None:
    preset = pb.f_msg(4, pb.f_int(1, 1) + pb.f_int(2, 3)) + pb.f_msg(
        5, pb.f_int(1, 1) + pb.f_int(2, 3)
    )
    decoded = decode_freo_preset(preset)
    assert decoded[FreoStrategy.SUCTION] == 3
    assert decoded[FreoStrategy.COVERAGE] is None

    config = pb.f_msg(29, preset)
    client = FakeClient([pb.f_msg(2, config)])
    import asyncio

    asyncio.run(client.set_freo_strategy(FreoStrategy.COVERAGE, 2))
    topic, payload = client.sent[-1]
    assert topic == "config/set"
    written = decode_freo_preset(pb.first(payload, 29))
    assert written[FreoStrategy.COVERAGE] == 2
    assert written[FreoStrategy.SUCTION] == 3  # untouched


def test_go_to_point_matches_app_frame() -> None:
    client = FakeClient()
    import asyncio

    asyncio.run(client.go_to_point(10.0, -10.0, map_id=1))
    pose = pb.f_msg(1, pb.point(10.0, -10.0)) + pb.f_float(2, 0.0)
    assert client.sent == [
        ("telecontrol/point_navi", pb.f_int(3, 1) + pb.f_int(4, 2) + pb.f_msg(5, pose))
    ]


def test_remote_move_publishes_and_stops(monkeypatch) -> None:
    client = FakeClient()
    import asyncio

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    asyncio.run(client.remote_move(150, 30, duration=1.0, rate_hz=5))
    assert len(client.published) == 6
    assert client.published[0] == (
        "telecontrol/velocity_control",
        pb.f_int(1, 100) + pb.f_int(2, 30),
    )
    assert client.published[-1] == ("telecontrol/velocity_control", b"")


def test_zone_clean_payload() -> None:
    client = FakeClient()
    payload = client.build_zone_clean_payload(1, 26, -8, 10, -24, mode=2, fan=2, passes=1)
    task = pb.first(payload, 1)
    assert pb.first(task, 1) == 1 and pb.first(task, 5) == 1  # map, sweep task
    item = pb.first(task, 2)
    zone = pb.first(item, 1)
    assert pb.first(zone, 1) == 2  # ZONE_TYPE_ZONE
    corners = [
        (pb.as_signed(pb.first(p, 1)), pb.as_signed(pb.first(p, 2))) for p in pb.all_values(zone, 3)
    ]
    assert corners == [(10, -24), (26, -24), (26, -8), (10, -8)]
    param = pb.first(item, 2)
    assert pb.first(param, 5) == 1  # sweepTime
    assert pb.first(param, 9) is None


def test_zone_clean_freo_flags() -> None:
    client = FakeClient()
    payload = client.build_zone_clean_payload(1, 0, 0, 5, 5, freo=True)
    task = pb.first(payload, 1)
    assert pb.first(pb.first(pb.first(task, 2), 2), 9) == 1
    assert pb.first(pb.first(task, 3), 1) == 1


def test_pet_voices_decode() -> None:
    info = (
        pb.f_bytes(1, "voiceO17.mp3")
        + pb.f_bytes(2, "/userdata/usr/data/voice/voiceO17.mp3")
        + pb.f_bytes(3, "https://x/y.png")
    )
    dogs = pb.f_bytes(1, "icon") + pb.f_msg(3, info)
    cats = pb.f_bytes(1, "icon") + pb.f_int(2, 1) + pb.f_msg(3, info)
    client = FakeClient([pb.f_msg(1, dogs) + pb.f_msg(1, cats)])
    import asyncio

    voices = asyncio.run(client.get_pet_voices())
    assert [v.kind for v in voices] == ["dog", "cat"]
    assert voices[0].path.endswith("voiceO17.mp3")
    with pytest.raises(ValueError):
        asyncio.run(client.play_voice_file("/etc/passwd"))


def test_map_operation_retries_with_reported_edit_version() -> None:
    map_raw = pb.f_int(1, 1) + pb.f_int(2, 575) + pb.f_int(33, 100)
    stale = pb.f_int(1, 2) + pb.f_int(3, 124) + pb.f_int(4, 1)
    ok = pb.f_int(1, 1) + pb.f_int(3, 125)
    client = FakeClient([pb.f_msg(2, map_raw), stale, ok])
    import asyncio

    result = asyncio.run(client.set_map_name("Casa"))
    assert result["success"] is True
    first, second = client.sent[1][1], client.sent[2][1]
    assert pb.first(first, 3) == 100
    assert pb.first(second, 3) == 124


def test_no_go_zone_uses_float_points() -> None:
    map_raw = pb.f_int(1, 1) + pb.f_int(2, 1) + pb.f_int(33, 1)
    client = FakeClient([pb.f_msg(2, map_raw), pb.f_int(1, 1)])
    import asyncio

    asyncio.run(client.add_no_go_zone([(58, 24), (73, 24), (73, 39)], zone_id=7))
    op = pb.first(client.sent[-1][1], 4)
    area = pb.first(pb.first(op, 4), 2)
    first_point = pb.first(area, 1)
    assert pb.as_float(pb.first(first_point, 1)) == 58.0
    assert pb.first(area, 3) == 7


def test_language_set_is_confirmed_by_read_back() -> None:
    # set answers FAILED (2) but the robot switches; get then reports English (3)
    client = FakeClient([pb.f_int(1, 2), pb.f_int(1, 1) + pb.f_int(2, 3)])
    import asyncio

    assert asyncio.run(client.set_language(3)) is True


def _config_reply(config: bytes) -> bytes:
    return pb.f_msg(2, config)


def test_decode_config_messages() -> None:
    from narwal_client.extended import decode_config_messages

    order = pb.f_int(1, 1) + pb.f_packed(2, [8, 9, 4]) + pb.f_packed(3, [2, 1]) + pb.f_int(4, 1)
    config = (
        pb.f_msg(4, pb.f_int(2, 79200) + pb.f_int(3, 28800))
        + pb.f_int(10, 1)
        + pb.f_msg(14, pb.f_int(1, 3) + pb.f_int(2, 7))
        + pb.f_msg(18, pb.f_int(1, 1) + pb.f_int(2, 2) + pb.f_msg(4, order))
        + pb.f_msg(38, pb.f_int(3, 21600))
        + pb.f_msg(44, pb.f_msg(1, pb.f_int(1, 1) + pb.f_int(2, 1)))
    )
    values = decode_config_messages(config)
    assert values["do_not_disturb"] == {"enabled": False, "begin": 79200, "end": 28800}
    assert values["off_peak_charging"]["duration"] == 21600
    assert values["corner_frequency"] == {"frequency": 3, "interval": 7}
    assert values["rewash_strategy"] == 1
    assert values["room_orders"][1]["order"] == [8, 9, 4]
    assert values["no_stairs"] is True and values["no_stairs_maps"] == {1: True}


def test_dnd_edit_keeps_other_fields() -> None:
    import asyncio

    config = pb.f_msg(4, pb.f_int(2, 79200) + pb.f_int(3, 28800))
    client = FakeClient([_config_reply(config)])
    asyncio.run(client.set_do_not_disturb(enabled=True))
    topic, payload = client.sent[-1]
    body = pb.first(payload, 5)  # Set field 5 (Get 4)
    assert topic == "config/set"
    assert pb.first(body, 1) == 1 and pb.first(body, 2) == 79200 and pb.first(body, 3) == 28800


def test_off_peak_minimum_duration() -> None:
    import asyncio

    client = FakeClient()
    with pytest.raises(ValueError):
        asyncio.run(client.set_off_peak_charging(duration=7200))


def test_room_order_replaces_only_its_map() -> None:
    import asyncio

    other = pb.f_int(1, 1) + pb.f_packed(2, [1]) + pb.f_int(4, 2)
    mine = pb.f_int(1, 1) + pb.f_packed(2, [8, 9]) + pb.f_int(4, 1)
    strategy = pb.f_int(1, 1) + pb.f_msg(4, mine) + pb.f_msg(4, other)
    client = FakeClient([_config_reply(pb.f_msg(18, strategy))])
    asyncio.run(client.set_room_order([9, 8, 3], map_id=1))
    body = pb.first(client.sent[-1][1], 18)
    units = pb.all_values(body, 4)
    assert len(units) == 2
    assert pb.first(units[0], 2) == bytes([9, 8, 3])
    assert units[1] == other
    assert pb.first(body, 1) == 1  # strategy untouched


def test_no_stairs_writes_flag_and_map_mirror() -> None:
    import asyncio

    mirror = pb.f_msg(1, pb.f_int(1, 1) + pb.f_int(2, 1))
    client = FakeClient([pb.f_int(1, 1), _config_reply(pb.f_msg(44, mirror))])
    asyncio.run(client.set_no_stairs(False, map_id=1))
    assert client.sent[0] == ("config/set", pb.f_int(11, 0))
    unit = pb.first(pb.first(client.sent[-1][1], 44), 1)
    assert pb.first(unit, 1) == 1 and pb.first(unit, 2) is None


def test_add_threshold_reports_robot_id() -> None:
    import asyncio

    rev = pb.f_int(1, 1) + pb.f_int(2, 1) + pb.f_int(33, 1)
    line = pb.f_int(1, 1) + pb.f_msg(2, pb.point(1, 2)) + pb.f_msg(3, pb.point(3, 4))
    client = FakeClient(
        [pb.f_msg(2, rev), pb.f_msg(2, rev), pb.f_int(1, 1), pb.f_msg(2, rev + pb.f_msg(41, line))]
    )
    result = asyncio.run(client.add_threshold((1, 2), (3, 4)))
    assert result["success"] and result["id"] == 1


def test_split_and_merge_payloads() -> None:
    import asyncio

    rev = pb.f_int(1, 1) + pb.f_int(2, 1) + pb.f_int(33, 1)
    rooms = pb.f_msg(12, pb.f_int(1, 4)) + pb.f_msg(12, pb.f_int(1, 9))
    client = FakeClient([pb.f_msg(2, rev + rooms), pb.f_msg(2, rev), pb.f_int(1, 1)])
    result = asyncio.run(client.split_room(4, (1.0, 2.0), (3.0, 4.0)))
    seg = pb.first(pb.first(client.sent[-1][1], 4), 1)
    assert pb.first(seg, 3) == 4 and pb.first(seg, 4) == 10 and result["new_room_id"] == 10

    client = FakeClient([pb.f_msg(2, rev), pb.f_int(1, 1)])
    asyncio.run(client.merge_rooms(4, 9))
    merge = pb.first(pb.first(client.sent[-1][1], 4), 2)
    assert (pb.first(merge, 1), pb.first(merge, 2), pb.first(merge, 3)) == (4, 9, 4)


def test_rotate_map_rejects_odd_angles() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(FakeClient().rotate_map(45))


def test_zone_clean_with_several_rectangles() -> None:
    client = FakeClient()
    payload = client.build_zone_clean_payload(
        1, 0, 0, 5, 5, mode=2, extra_zones=[(10, 10, 20, 20), (30, -5, 40, 5)]
    )
    task = pb.first(payload, 1)
    items = pb.all_values(task, 2)
    assert len(items) == 3
    assert [pb.first(i, 3) for i in items] == [1, 2, 3]  # cleaning order
    last_zone = pb.first(items[2], 1)
    corners = [
        (pb.as_signed(pb.first(p, 1)), pb.as_signed(pb.first(p, 2)))
        for p in pb.all_values(last_zone, 3)
    ]
    assert corners[0] == (30, -5) and corners[2] == (40, 5)
