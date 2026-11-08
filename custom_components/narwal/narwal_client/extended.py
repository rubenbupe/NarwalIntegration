"""Extended local commands for the Narwal Flow (AX12), verified on firmware v01.08.03.07.

Every command here was exercised against a real robot over the local
WebSocket; formats come from the Narwal app's protobuf definitions. See
docs/local-protocol-extensions.md for the evidence and caveats.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from .pb import (
    WIRE_VARINT,
    all_values,
    as_double,
    as_float,
    as_signed,
    f_bytes,
    f_float,
    f_int,
    f_msg,
    f_packed,
    first,
    grid_point,
    iter_fields,
    point,
    read_varint,
    replace_field,
)

if TYPE_CHECKING:  # pragma: no cover
    from .models import CommandResponse

_LOGGER = logging.getLogger(__name__)

TOPIC_CMD_CONFIG_GET = "config/get"
TOPIC_CMD_CONFIG_SET = "config/set"
TOPIC_CMD_LANGUAGE_GET = "config/language/get"
TOPIC_CMD_LANGUAGE_SET = "config/language/set"
TOPIC_CMD_SUPPORTED_LANGUAGES = "config/supported_languages/get"
TOPIC_CMD_VOICE_LIST = "voice/get_voice_list"
TOPIC_CMD_PLAY_VOICE = "developer/play_voice"
TOPIC_CMD_SEARCH_PET = "pet/search_pet"
TOPIC_CMD_POINT_NAVI = "telecontrol/point_navi"
TOPIC_CMD_STOP_POINT_NAVI = "telecontrol/stop_point_navi"
TOPIC_CMD_MANUAL_CONTROL = "telecontrol/set_manual_control_mode"
TOPIC_CMD_VELOCITY = "telecontrol/velocity_control"
TOPIC_CMD_WASH_AND_DRY_MOP = "supply/wash_and_dry_mop"
TOPIC_CMD_RESET_CONSUMABLE = "consumable/reset_consumable_info"
TOPIC_CMD_BATTERY_INFO = "info/battery_info"
TOPIC_CMD_MAP_OPERATION = "map/operation"
TOPIC_CMD_KEY_MAPPING_GET = "config/key_mapping/get"

SERVICE_RESULT_SUCCESS = 1
ERROR_CODE_UNMATCHED_EDIT_VERSION = 1


@dataclass(frozen=True)
class ConfigField:
    """One robot setting: SetConfig_Request field, Config (get) field, and kind."""

    set_field: int
    get_field: int
    kind: str  # "bool" | "int" | "enum"
    options: tuple[int, ...] = ()


# Settings verified with set/read-back/restore. Set and Get numbers differ after
# field 9. Excluded on purpose: language/region/privacy (other topics or consent),
# No stairs (safety confirmation in the app, per-map mirror), and fields that answer
# GENERAL_ERROR on the AX12 (39, 40, 50-54).
CONFIG_FIELDS: dict[str, ConfigField] = {
    "volume": ConfigField(1, 1, "int"),
    "plateau_mode": ConfigField(6, 5, "bool"),
    "auto_detergent": ConfigField(7, 6, "bool"),
    "child_lock": ConfigField(8, 7, "bool"),
    "carpet_cleaning": ConfigField(9, 8, "bool"),
    "dirt_detection": ConfigField(12, 11, "bool"),
    "mop_drying_strength": ConfigField(13, 12, "enum", (1, 2, 3)),
    "rewash_area": ConfigField(14, 13, "enum", (1, 2, 3)),
    "carpet_mode": ConfigField(15, 15, "enum", (1, 2, 3, 4)),
    "carpet_customize": ConfigField(16, 16, "bool"),
    "new_carpet_mode": ConfigField(17, 17, "enum", (1, 2, 3, 4)),
    "freo_intensity": ConfigField(19, 19, "enum", (1, 2, 3)),
    "dock_default_mode": ConfigField(21, 21, "enum", (2, 3, 4, 5)),
    "pet_mode": ConfigField(22, 22, "bool"),
    "smart_deep_clean": ConfigField(23, 23, "bool"),
    "obstacle_photos": ConfigField(24, 24, "bool"),
    "moisture_proof_pad": ConfigField(26, 26, "bool"),
    "auto_empty": ConfigField(27, 27, "bool"),
    "smart_auto_empty": ConfigField(28, 28, "bool"),
    "quiet_auto_empty": ConfigField(30, 30, "bool"),
    "dust_bag_disinfection": ConfigField(31, 31, "bool"),
    "hot_water_mop_wash": ConfigField(32, 32, "bool"),
    "intensive_large_soiled": ConfigField(33, 33, "bool"),
    "obstacle_avoidance": ConfigField(34, 34, "enum", (1, 2, 3)),
    "robot_default_mode": ConfigField(35, 35, "enum", (2, 3, 4, 5)),
    "voice_assistant": ConfigField(36, 36, "bool"),
    "auto_power_off": ConfigField(37, 37, "bool"),
    "corner_cleaning": ConfigField(41, 41, "enum", (1, 3)),
    "pet_icon": ConfigField(42, 42, "enum", (1, 2, 3)),
    "dust_sensor_detection": ConfigField(43, 43, "bool"),
    "carpet_priority": ConfigField(45, 46, "enum", (1, 2)),
    "carpet_focus": ConfigField(46, 47, "enum", (1, 2)),
    "recognize_all_objects": ConfigField(47, 49, "bool"),
    "precious_item_guard": ConfigField(48, 50, "bool"),
    "station_light": ConfigField(49, 51, "enum", (1, 2)),
}

CONFIG_FIELD_FREO_PRESET = 29  # same number in Set and Get


class FreoStrategy(IntEnum):
    """Sub-fields of smartCleanParam (config 29). Absent = Smart (AI decides)."""

    REMOP_LIMIT = 1  # 1..3 cycles
    COVERAGE = 2  # 1 standard, 2 intensive
    SUCTION = 4  # 1 quiet, 2 standard, 3 strong, 4 super powerful, 5 super
    MOP_HUMIDITY = 5  # 1 slightly dry, 2 standard, 3 high


class ManualControlMode(IntEnum):
    """telecontrol/set_manual_control_mode values."""

    OFF = 0
    JOYSTICK = 1
    CLEAN_DEFAULT = 2
    SWEEP_MUTE = 3
    SWEEP_NORMAL = 4
    SWEEP_STRONG = 5
    SWEEP_DEEP = 6
    SWEEP_SUPER = 7


class ForbiddenType(IntEnum):
    """ForbiddenArea.type."""

    SWEEP = 1
    MOP = 2
    SWEEP_MOP = 3


class FloorTexture(IntEnum):
    """RoomInfo.texture."""

    WOOD = 1
    TILE = 2
    COBBLESTONE = 3
    TATAMI = 4
    MARBLE = 5


LANGUAGES: dict[int, str] = {
    1: "zh_hans",
    2: "zh_hant",
    3: "en",
    4: "ko",
    5: "ja",
    6: "de",
    7: "he",
    8: "it",
    9: "fr",
    10: "es",
    11: "ru",
    12: "pl",
    13: "tr",
    14: "th",
    15: "vi",
}

VOICE_TYPE_TEASE_PET = 1
PET_VOICE_KINDS = {0: "dog", 1: "cat", 2: "record", 3: "robot"}


@dataclass(frozen=True)
class PetVoice:
    """A voice clip stored on the robot."""

    kind: str
    name: str
    path: str
    image_url: str


@dataclass(frozen=True)
class MapRevision:
    """Identifiers every map/operation needs."""

    map_id: int
    map_version: int
    edit_version: int


def decode_config(config: bytes) -> dict[str, Any]:
    """Decode the Config message (config/get field 2) into CONFIG_FIELDS names."""
    by_get = {spec.get_field: name for name, spec in CONFIG_FIELDS.items()}
    values: dict[str, Any] = {name: None for name in CONFIG_FIELDS}
    for spec_name, spec in CONFIG_FIELDS.items():
        if spec.kind == "bool":
            values[spec_name] = False  # absent bool = false
    for field, wire, value, _raw in iter_fields(config):
        name = by_get.get(field)
        if name is None or wire != WIRE_VARINT:
            continue
        kind = CONFIG_FIELDS[name].kind
        values[name] = bool(value) if kind == "bool" else int(value)
    return values


def decode_freo_preset(preset: bytes | None) -> dict[FreoStrategy, int | None]:
    """Decode smartCleanParam: each strategy is {1: customized, 2: value}; absent = Smart."""
    result: dict[FreoStrategy, int | None] = {s: None for s in FreoStrategy}
    if not preset:
        return result
    for field, _wire, value, _raw in iter_fields(preset):
        try:
            strategy = FreoStrategy(field)
        except ValueError:
            continue
        if isinstance(value, bytes):
            level = first(value, 2)
            result[strategy] = int(level) if isinstance(level, int) else None
    return result


class CornerFrequency(IntEnum):
    """CleanCornerSetting.cleanCornerFreq."""

    OFF = 1
    EVERY_N_CLEANS = 2
    EVERY_N_DAYS = 3


class RewashStrategy(IntEnum):
    """CustomCleanStrategy.strategy (Floor Cleaning screen)."""

    BY_AREA = 1
    BY_ROOM = 2
    SMART = 3


# Sub-message settings: name -> (Config get field, SetConfig_Request field).
CONFIG_MESSAGES: dict[str, tuple[int, int]] = {
    "do_not_disturb": (4, 5),
    "corner_frequency": (14, 10),
    "clean_strategy": (18, 18),
    "off_peak_charging": (38, 38),
    "map_configuration": (44, 44),
}
CONFIG_FIELD_NO_STAIRS_SET = 11
OFF_PEAK_MIN_DURATION = 3 * 3600
CONFIG_FIELD_NO_STAIRS_GET = 10


def _seconds(value: int) -> int:
    """Clamp a time of day to 0..86399 seconds."""
    return max(0, min(86399, int(value)))


def _packed_ints(value: int | bytes | None) -> list[int]:
    if isinstance(value, int):
        return [value]
    out: list[int] = []
    pos = 0
    data = value or b""
    while pos < len(data):
        item, pos = read_varint(data, pos)
        out.append(item)
    return out


def decode_config_messages(config: bytes) -> dict[str, Any]:
    """Decode the sub-message settings of a Config message."""

    def msg(name: str) -> bytes:
        value = first(config, CONFIG_MESSAGES[name][0])
        return value if isinstance(value, bytes) else b""

    dnd = msg("do_not_disturb")
    valley = msg("off_peak_charging")
    corner = msg("corner_frequency")
    strategy = msg("clean_strategy")
    orders: dict[int, dict[str, Any]] = {}
    for value in all_values(strategy, 4):
        if isinstance(value, bytes):
            map_id = int(first(value, 4) or 0)
            orders[map_id] = {
                "custom": bool(first(value, 1)),
                "order": [v for v in _all_packed(value, 2)],
                "default": [v for v in _all_packed(value, 3)],
            }
    no_stairs_maps = {}
    for value in all_values(msg("map_configuration"), 1):
        if isinstance(value, bytes):
            no_stairs_maps[int(first(value, 1) or 0)] = bool(first(value, 2))
    return {
        "do_not_disturb": {
            "enabled": bool(first(dnd, 1)),
            "begin": int(first(dnd, 2) or 0),
            "end": int(first(dnd, 3) or 0),
        },
        "off_peak_charging": {
            "enabled": bool(first(valley, 1)),
            "begin": int(first(valley, 2) or 0),
            "duration": int(first(valley, 3) or 0),
        },
        "corner_frequency": {
            "frequency": int(first(corner, 1) or 0),
            "interval": int(first(corner, 2) or 0),
        },
        "rewash_strategy": int(first(strategy, 1) or 0),
        "room_orders": orders,
        "no_stairs": bool(first(config, CONFIG_FIELD_NO_STAIRS_GET)),
        "no_stairs_maps": no_stairs_maps,
    }


def _all_packed(message: bytes, field: int) -> list[int]:
    out: list[int] = []
    for value in all_values(message, field):
        out.extend(_packed_ints(value))
    return out


class ExtendedCommandsMixin:
    """Commands mixed into NarwalClient. Relies on send_command / _send_unawaited."""

    # --- settings (config/get, config/set) ---

    async def get_config_raw(self) -> bytes:
        """Return the raw Config message from config/get."""
        resp = await self.send_command(TOPIC_CMD_CONFIG_GET)  # type: ignore[attr-defined]
        config = first(resp.raw_payload, 2)
        return config if isinstance(config, bytes) else b""

    async def get_config(self) -> dict[str, Any]:
        """Return every known setting by name, plus the Freo preset and sub-messages."""
        raw = await self.get_config_raw()
        values = decode_config(raw)
        preset = first(raw, CONFIG_FIELD_FREO_PRESET)
        values["freo_preset"] = decode_freo_preset(preset if isinstance(preset, bytes) else None)
        values.update(decode_config_messages(raw))
        return values

    async def _set_config_message(
        self, name: str, edit: Callable[[bytes], bytes]
    ) -> CommandResponse:
        """Read a sub-message setting, let edit() change it, write it back whole."""
        get_field, set_field = CONFIG_MESSAGES[name]
        raw = await self.get_config_raw()
        current = first(raw, get_field)
        body = edit(current if isinstance(current, bytes) else b"")
        return await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_CONFIG_SET, f_msg(set_field, body)
        )

    async def set_do_not_disturb(
        self, enabled: bool | None = None, begin: int | None = None, end: int | None = None
    ) -> CommandResponse:
        """Do Not Disturb: enabled, begin/end in seconds from midnight."""

        def edit(msg: bytes) -> bytes:
            if enabled is not None:
                msg = replace_field(msg, 1, f_int(1, True) if enabled else None)
            if begin is not None:
                msg = replace_field(msg, 2, f_int(2, _seconds(begin)))
            if end is not None:
                msg = replace_field(msg, 3, f_int(3, _seconds(end)))
            return msg

        return await self._set_config_message("do_not_disturb", edit)

    async def set_off_peak_charging(
        self, enabled: bool | None = None, begin: int | None = None, duration: int | None = None
    ) -> CommandResponse:
        """Off-peak charging: begin (s from midnight), duration in seconds.

        The robot rejects durations under 3 hours (verified: 1 h and 2 h fail,
        3 h passes) and an absent duration, so one is always kept.
        """
        if duration is not None and not OFF_PEAK_MIN_DURATION <= duration <= 86400:
            raise ValueError("off-peak duration must be between 3 and 24 hours")

        def edit(msg: bytes) -> bytes:
            if enabled is not None:
                msg = replace_field(msg, 1, f_int(1, True) if enabled else None)
            if begin is not None:
                msg = replace_field(msg, 2, f_int(2, _seconds(begin)))
            if duration is not None:
                msg = replace_field(msg, 3, f_int(3, duration))
            if not isinstance(first(msg, 3), int):  # robot rejects duration 0
                msg = replace_field(msg, 3, f_int(3, 6 * 3600))
            return msg

        return await self._set_config_message("off_peak_charging", edit)

    async def set_corner_frequency(
        self, frequency: CornerFrequency | int, interval: int | None = None
    ) -> CommandResponse:
        """Intensive corner cleaning frequency: off, every N cleans or every N days."""

        def edit(msg: bytes) -> bytes:
            msg = replace_field(msg, 1, f_int(1, int(frequency)))
            if interval is not None:
                msg = replace_field(msg, 2, f_int(2, max(1, interval)))
            return msg

        return await self._set_config_message("corner_frequency", edit)

    async def set_rewash_strategy(self, strategy: RewashStrategy | int) -> CommandResponse:
        """Floor cleaning rewash: by area (rewash_area setting) or smart zone-based."""
        return await self._set_config_message(
            "clean_strategy", lambda msg: replace_field(msg, 1, f_int(1, int(strategy)))
        )

    async def set_room_order(self, room_ids: list[int], map_id: int) -> CommandResponse:
        """Custom room cleaning order for a map (customCleanStrategy.cleanOrders)."""
        if not room_ids:
            raise ValueError("room order is empty")

        def edit(msg: bytes) -> bytes:
            parts = []
            replaced = False
            for num, _w, value, raw in iter_fields(msg):
                if num == 4 and isinstance(value, bytes) and first(value, 4) == map_id:
                    unit = replace_field(value, 1, f_int(1, True))
                    unit = replace_field(unit, 2, f_packed(2, room_ids))
                    parts.append(f_msg(4, unit))
                    replaced = True
                else:
                    parts.append(raw)
            if not replaced:
                parts.append(f_msg(4, f_int(1, True) + f_packed(2, room_ids) + f_int(4, map_id)))
            return b"".join(parts)

        return await self._set_config_message("clean_strategy", edit)

    async def set_no_stairs(self, enabled: bool, map_id: int) -> CommandResponse:
        """No stairs (boost crossing): global flag plus the per-map mirror the app keeps."""
        response = await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_CONFIG_SET, f_int(CONFIG_FIELD_NO_STAIRS_SET, enabled)
        )

        def edit(msg: bytes) -> bytes:
            parts = []
            replaced = False
            for num, _w, value, raw in iter_fields(msg):
                if num == 1 and isinstance(value, bytes) and first(value, 1) == map_id:
                    parts.append(f_msg(1, f_int(1, map_id) + (f_int(2, True) if enabled else b"")))
                    replaced = True
                else:
                    parts.append(raw)
            if not replaced:
                parts.append(f_msg(1, f_int(1, map_id) + (f_int(2, True) if enabled else b"")))
            return b"".join(parts)

        await self._set_config_message("map_configuration", edit)
        return response

    async def set_config(self, name: str, value: int | bool) -> CommandResponse:
        """Write one setting. SetConfig_Request is a oneof: one field per call."""
        spec = CONFIG_FIELDS[name]
        if spec.kind == "enum" and int(value) not in spec.options:
            raise ValueError(f"{name}: {value} not in {spec.options}")
        if spec.kind == "int" and not 0 <= int(value) <= 100:
            raise ValueError(f"{name}: {value} out of range 0-100")
        payload = f_int(spec.set_field, value)
        return await self.send_command(TOPIC_CMD_CONFIG_SET, payload)  # type: ignore[attr-defined]

    async def set_freo_strategy(self, strategy: FreoStrategy, value: int | None) -> CommandResponse:
        """Set one Freo Mind preset strategy; None returns it to Smart (AI).

        The preset is written whole, so the current one is read first and only
        the requested sub-field changes.
        """
        raw = await self.get_config_raw()
        current = first(raw, CONFIG_FIELD_FREO_PRESET)
        preset = current if isinstance(current, bytes) else b""
        entry = (
            f_msg(int(strategy), f_int(1, True) + f_int(2, value)) if value is not None else None
        )
        preset = replace_field(preset, int(strategy), entry)
        payload = f_msg(CONFIG_FIELD_FREO_PRESET, preset)
        return await self.send_command(TOPIC_CMD_CONFIG_SET, payload)  # type: ignore[attr-defined]

    # --- language ---

    async def get_language(self) -> int | None:
        """Robot voice language (Language enum)."""
        resp = await self.send_command(TOPIC_CMD_LANGUAGE_GET)  # type: ignore[attr-defined]
        value = first(resp.raw_payload, 2)
        return int(value) if isinstance(value, int) else None

    async def get_supported_languages(self) -> list[int]:
        """Languages this robot can switch to."""
        resp = await self.send_command(TOPIC_CMD_SUPPORTED_LANGUAGES)  # type: ignore[attr-defined]
        values: list[int] = []
        for value in all_values(resp.raw_payload, 2):
            if isinstance(value, int):
                values.append(value)
            elif isinstance(value, bytes):  # packed
                pos = 0
                while pos < len(value):
                    item, pos = read_varint(value, pos)
                    values.append(item)
        return values

    async def set_language(self, language: int, timeout: float = 60.0) -> bool:
        """Switch the robot voice language.

        The robot answers RESULT_TYPE_FAILED even when it switches (it may
        download a voice pack first), so success is decided by reading back.
        """
        await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_LANGUAGE_SET, f_int(1, language), timeout=timeout
        )
        return await self.get_language() == language

    # --- pets ---

    async def get_pet_voices(self) -> list[PetVoice]:
        """Dog and cat clips stored on the robot (voice/get_voice_list TeasePet)."""
        resp = await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_VOICE_LIST, f_int(1, VOICE_TYPE_TEASE_PET)
        )
        voices: list[PetVoice] = []
        for group in all_values(resp.raw_payload, 1):
            if not isinstance(group, bytes):
                continue
            kind_value = first(group, 2)
            kind = PET_VOICE_KINDS.get(int(kind_value) if isinstance(kind_value, int) else 0, "dog")
            for info in all_values(group, 3):
                if not isinstance(info, bytes):
                    continue
                name, path, image = (first(info, n) for n in (1, 2, 3))
                if isinstance(path, bytes):
                    voices.append(
                        PetVoice(
                            kind=kind,
                            name=name.decode(errors="replace") if isinstance(name, bytes) else "",
                            path=path.decode(errors="replace"),
                            image_url=(
                                image.decode(errors="replace") if isinstance(image, bytes) else ""
                            ),
                        )
                    )
        return voices

    async def play_voice_file(self, path: str) -> CommandResponse:
        """Play a clip stored on the robot (developer/play_voice, resourceType FILE)."""
        if not path.startswith("/userdata/"):
            raise ValueError("voice path must be a robot path under /userdata/")
        payload = f_int(4, 2) + f_bytes(5, path)
        return await self.send_command(TOPIC_CMD_PLAY_VOICE, payload)  # type: ignore[attr-defined]

    async def search_pet(self) -> CommandResponse:
        """Start Pet Finding (TASK_TYPE_SEARCH_PET). Stop with task/force_end."""
        return await self.send_command(TOPIC_CMD_SEARCH_PET, timeout=15.0)  # type: ignore[attr-defined]

    # --- movement ---

    async def go_to_point(
        self, x_dm: float, y_dm: float, map_id: int, theta: float = 0.0
    ) -> CommandResponse:
        """Drive to a map point (float32 decimetres), as the app's video map does.

        PointNavi_Request{3 mapId, 4 exploreMode 2, 5 points[PoseData]}. Sending
        field 1 (poseData) without mapId makes the robot drop the socket.
        """
        pose = f_msg(1, point(x_dm, y_dm)) + f_float(2, theta)
        payload = f_int(3, map_id) + f_int(4, 2) + f_msg(5, pose)
        return await self.send_command(TOPIC_CMD_POINT_NAVI, payload, timeout=20.0)  # type: ignore[attr-defined]

    async def stop_go_to_point(self) -> CommandResponse:
        """Stop point navigation."""
        return await self.send_command(TOPIC_CMD_STOP_POINT_NAVI)  # type: ignore[attr-defined]

    async def set_manual_control(self, mode: ManualControlMode | int) -> CommandResponse:
        """Enter/leave remote control. OFF sends the robot back to the dock."""
        return await self.send_command(TOPIC_CMD_MANUAL_CONTROL, f_int(1, int(mode)))  # type: ignore[attr-defined]

    async def send_velocity(self, linear: int, angular: int) -> None:
        """One TelecontrolVelocity sample (publish, no response). App range about ±100."""
        payload = b""
        if linear:
            payload += f_int(1, linear)
        if angular:
            payload += f_int(2, angular)
        await self._send_unawaited(TOPIC_CMD_VELOCITY, payload)  # type: ignore[attr-defined]

    async def remote_move(
        self, linear: int, angular: int, duration: float, rate_hz: float = 5.0
    ) -> None:
        """Joystick move for duration seconds, then stop. Caller enables JOYSTICK first."""
        linear = max(-100, min(100, int(linear)))
        angular = max(-100, min(100, int(angular)))
        steps = max(1, int(duration * rate_hz))
        for _ in range(steps):
            await self.send_velocity(linear, angular)
            await asyncio.sleep(1.0 / rate_hz)
        await self.send_velocity(0, 0)

    # --- dock and consumables ---

    async def wash_and_dry_mop(self) -> CommandResponse:
        """Idle mop wash as the app's dock button does (wash, then dry)."""
        return await self.send_command(TOPIC_CMD_WASH_AND_DRY_MOP)  # type: ignore[attr-defined]

    async def reset_consumables(
        self, maintain: list[int] | None = None, replace: list[int] | None = None
    ) -> CommandResponse:
        """Clear consumable alerts and reset their timers."""
        body = b""
        if maintain:
            body += f_packed(1, maintain)
        if replace:
            body += f_packed(2, replace)
        if not body:
            raise ValueError("nothing to reset")
        return await self.send_command(TOPIC_CMD_RESET_CONSUMABLE, f_msg(1, body))  # type: ignore[attr-defined]

    async def get_battery_info(self) -> dict[str, Any]:
        """Battery health (info/battery_info): capacity, cycles, health, temperature..."""
        resp = await self.send_command(TOPIC_CMD_BATTERY_INFO)  # type: ignore[attr-defined]
        raw = resp.raw_payload
        return {
            "full_charge_capacity": first(raw, 1),
            "charge_cycles": first(raw, 2),
            "soc": first(raw, 4),
            "temperature": as_double(first(raw, 5)),
            "voltage": as_double(first(raw, 6)),
            "current": as_double(first(raw, 7)),
            "health": first(raw, 8),
            "rated_capacity": first(raw, 9),
        }

    # --- cleaning extras ---

    def build_zone_clean_payload(
        self,
        map_id: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        mode: int = 4,
        fan: int = 2,
        water: int = 2,
        passes: int = 1,
        freo: bool = False,
        extra_zones: list[tuple[int, int, int, int]] | None = None,
    ) -> bytes:
        """CleanTask for one or more rectangles in grid cells (ZoneOption.zoneType 2).

        Each rectangle becomes its own CleanItem, cleaned in the given order.
        """
        # Pass count lives in sweepTime (5), mopTime (6) or sweepMopSyncTime (7)
        pass_fields = {2: (5,), 3: (6,), 4: (7,), 5: (5, 6)}.get(mode, (7,))
        param = f_int(1, mode) + f_int(2, fan) + f_int(4, water)
        param += b"".join(f_int(f, passes) for f in pass_fields)
        if freo:
            param += f_int(9, True)
        items = b""
        for order, (ax, ay, bx, by) in enumerate([(x1, y1, x2, y2), *(extra_zones or [])], 1):
            xa, xb = sorted((int(ax), int(bx)))
            ya, yb = sorted((int(ay), int(by)))
            corners = ((xa, ya), (xb, ya), (xb, yb), (xa, yb))
            bounds = b"".join(f_msg(3, grid_point(x, y)) for x, y in corners)
            bounds += b"".join(f_msg(4, grid_point(x, y)) for x, y in corners)
            zone = f_int(1, 2) + bounds
            items += f_msg(2, f_msg(1, zone) + f_msg(2, param) + f_int(3, order))
        option = f_int(1, True) if freo else b""
        task_type = {2: 1, 3: 2, 4: 4, 5: 3}.get(mode, 4)
        task = f_int(1, map_id) + items + f_msg(3, option) + f_int(5, task_type)
        return f_msg(1, task)

    async def clean_zone(
        self, map_id: int, x1: int, y1: int, x2: int, y2: int, **kw: Any
    ) -> CommandResponse:
        """Clean a rectangle of grid cells (robot must be docked)."""
        payload = self.build_zone_clean_payload(map_id, x1, y1, x2, y2, **kw)
        return await self.send_command("clean/start_clean", payload, timeout=10.0)  # type: ignore[attr-defined]

    # --- map editing (map/operation) ---

    async def get_map_raw(self) -> bytes:
        """Raw StaticMapPayload (map/get_map field 2)."""
        resp = await self.send_command("map/get_map", timeout=15.0)  # type: ignore[attr-defined]
        value = first(resp.raw_payload, 2)
        return value if isinstance(value, bytes) else b""

    @staticmethod
    def map_revision(map_raw: bytes) -> MapRevision:
        """mapId (1), mapVersion (2) and editVersion (33) of a map payload."""
        values = {n: first(map_raw, n) for n in (1, 2, 33)}
        return MapRevision(int(values[1] or 0), int(values[2] or 0), int(values[33] or 0))

    async def map_operation(self, operation: bytes) -> dict[str, Any]:
        """Apply one MapOperationPayload; retries once with the editVersion the robot reports."""
        rev = self.map_revision(await self.get_map_raw())
        edit_version = rev.edit_version
        result: dict[str, Any] = {}
        for _ in range(2):
            req = (
                f_int(1, rev.map_id)
                + f_int(2, rev.map_version)
                + f_int(3, edit_version)
                + f_msg(4, operation)
            )
            resp = await self.send_command(TOPIC_CMD_MAP_OPERATION, req, timeout=15.0)  # type: ignore[attr-defined]
            raw = resp.raw_payload
            result = {
                "result": first(raw, 1),
                "edit_version": first(raw, 3),
                "error_code": first(raw, 4),
            }
            if result["error_code"] == ERROR_CODE_UNMATCHED_EDIT_VERSION and result["edit_version"]:
                edit_version = int(result["edit_version"])
                continue
            break
        result["success"] = result.get("result") == SERVICE_RESULT_SUCCESS
        return result

    @staticmethod
    def room_info_raw(map_raw: bytes, room_id: int) -> bytes:
        """Raw RoomInfo (map field 12) for room_id."""
        for value in all_values(map_raw, 12):
            if isinstance(value, bytes) and first(value, 1) == room_id:
                return value
        raise KeyError(room_id)

    async def update_room(
        self,
        room_id: int,
        *,
        name: str | None = None,
        floor: FloorTexture | int | None = None,
        heavy_dirty: bool | None = None,
    ) -> dict[str, Any]:
        """Rename a room, set its floor material or its heavy-dirty flag."""
        info = self.room_info_raw(await self.get_map_raw(), room_id)
        if name is not None:
            info = replace_field(info, 3, f_bytes(3, name) if name else None)
        if floor is not None:
            info = replace_field(info, 4, f_int(4, int(floor)))
        if heavy_dirty is not None:
            info = replace_field(info, 7, f_int(7, True) if heavy_dirty else None)
        return await self.map_operation(f_msg(3, f_msg(1, info)))

    async def add_no_go_zone(
        self,
        polygon: list[tuple[float, float]],
        zone_id: int,
        zone_type: ForbiddenType | int = ForbiddenType.SWEEP_MOP,
    ) -> dict[str, Any]:
        """Add a no-go / no-mop polygon. Points are grid cells as float32 (ints are rejected)."""
        if len(polygon) < 3:
            raise ValueError("polygon needs at least 3 points")
        area = b"".join(f_msg(1, point(x, y)) for x, y in polygon)
        area += f_int(2, int(zone_type)) + f_int(3, zone_id)
        return await self.map_operation(f_msg(4, f_int(1, 1) + f_msg(2, area)))

    async def add_room_ban(
        self, room_id: int, zone_id: int, zone_type: ForbiddenType | int = ForbiddenType.SWEEP_MOP
    ) -> dict[str, Any]:
        """Forbid a whole room (ForbiddenArea mode ROOM)."""
        area = f_int(2, int(zone_type)) + f_int(3, zone_id) + f_int(4, room_id) + f_int(5, 1)
        return await self.map_operation(f_msg(4, f_int(1, 1) + f_msg(2, area)))

    async def remove_no_go_zone(self, zone_id: int) -> dict[str, Any]:
        """Delete a no-go zone or room ban by id."""
        return await self.map_operation(f_msg(4, f_int(1, 2) + f_msg(2, f_int(3, zone_id))))

    async def set_map_name(self, name: str) -> dict[str, Any]:
        """Rename the map ("" clears it)."""
        return await self.map_operation(f_msg(7, f_bytes(1, name)))

    async def rotate_map(self, angle: int) -> dict[str, Any]:
        """Display rotation of the map in degrees (0, 90, 180, 270)."""
        if angle % 90:
            raise ValueError("rotation must be a multiple of 90")
        return await self.map_operation(f_msg(5, f_int(1, angle % 360)))

    async def add_carpet(
        self,
        polygon: list[tuple[int, int]],
        option: int = 2,
        deep_clean: bool = False,
    ) -> dict[str, Any]:
        """Draw a carpet (Grid int cells); option = CarpetCleanOption (1 cross .. 4 avoid)."""
        if len(polygon) < 3:
            raise ValueError("carpet needs at least 3 points")
        body = f_int(2, option) + f_int(3, 1)
        body += b"".join(f_msg(4, grid_point(int(x), int(y))) for x, y in polygon)
        if deep_clean:
            body += f_int(7, True)
        return await self._add_and_report_id(f_msg(6, body), "carpets")

    async def set_carpet_option(self, carpet_id: int, option: int) -> dict[str, Any]:
        """Change how one carpet is cleaned (CarpetOperation OPERATION_CARPET_CLEAN_OPTION)."""
        return await self.map_operation(f_msg(6, f_int(1, carpet_id) + f_int(2, option)))

    async def delete_carpet(self, carpet_id: int) -> dict[str, Any]:
        """Delete a carpet by id."""
        return await self.map_operation(f_msg(6, f_int(1, carpet_id) + f_int(3, 3)))

    async def add_threshold(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        height: int = 20,
    ) -> dict[str, Any]:
        """Enhanced obstacle crossing line (float cells); height in mm.

        The robot assigns the id; it is returned as result["id"].
        """
        data = f_msg(2, point(*start)) + f_msg(3, point(*end)) + f_int(4, height) + f_int(6, True)
        return await self._add_and_report_id(f_msg(11, f_int(1, 3) + f_msg(2, data)), "thresholds")

    async def _add_and_report_id(self, operation: bytes, kind: str) -> dict[str, Any]:
        """Apply an add operation and report the id the robot assigned."""
        before = {o["id"] for o in decode_map_objects(await self.get_map_raw())[kind]}
        result = await self.map_operation(operation)
        if result.get("success"):
            after = decode_map_objects(await self.get_map_raw())[kind]
            new_ids = [o["id"] for o in after if o["id"] not in before]
            result["id"] = new_ids[0] if new_ids else None
        return result

    async def delete_threshold(self, threshold_id: int) -> dict[str, Any]:
        """Delete an obstacle crossing line by id."""
        return await self.map_operation(f_msg(11, f_int(1, 2) + f_msg(2, f_int(1, threshold_id))))

    @staticmethod
    def furniture_raw(map_raw: bytes, furniture_id: int) -> bytes:
        """Raw Furniture from map field 32 (visionData) by id."""
        vision = first(map_raw, 32)
        for value in all_values(vision if isinstance(vision, bytes) else b"", 1):
            if isinstance(value, bytes) and first(value, 1) == furniture_id:
                return value
        raise KeyError(furniture_id)

    async def update_furniture(
        self,
        furniture_id: int,
        *,
        x: float | None = None,
        y: float | None = None,
        angle: float | None = None,
    ) -> dict[str, Any]:
        """Move or rotate a furniture item (centre in float cells, angle in degrees)."""
        item = self.furniture_raw(await self.get_map_raw(), furniture_id)
        if x is not None or y is not None:
            box = first(item, 3)
            box = box if isinstance(box, bytes) else b""
            centre = first(box, 1)
            centre = centre if isinstance(centre, bytes) else b""
            cx = x if x is not None else as_float(first(centre, 1)) or 0.0
            cy = y if y is not None else as_float(first(centre, 2)) or 0.0
            box = replace_field(box, 1, f_msg(1, point(cx, cy)))
            item = replace_field(item, 3, f_msg(3, box))
        if angle is not None:
            item = replace_field(item, 4, f_float(4, angle))
        return await self.map_operation(f_msg(8, f_int(1, 1) + f_msg(2, item)))

    async def delete_furniture(self, furniture_id: int) -> dict[str, Any]:
        """Remove a furniture item from the map."""
        item = self.furniture_raw(await self.get_map_raw(), furniture_id)
        return await self.map_operation(f_msg(8, f_int(1, 2) + f_msg(2, item)))

    async def add_furniture(self, furniture: bytes) -> dict[str, Any]:
        """Add (or restore) a furniture item from its raw Furniture message."""
        return await self.map_operation(f_msg(8, f_int(1, 1) + f_msg(2, furniture)))

    async def split_room(
        self,
        room_id: int,
        start: tuple[float, float],
        end: tuple[float, float],
        new_room_id: int | None = None,
    ) -> dict[str, Any]:
        """Split a room along a line (float cells), as the app's room editor does.

        RoomSegOperation{startSegPoint, endSegPoint, leftRoomId = room_id,
        rightRoomId = new id (max room id + 1 unless given)}. Not exercised on a
        live map: it changes room ids that tasks and Home Assistant areas use.
        """
        if new_room_id is None:
            ids = [
                int(first(v, 1) or 0)
                for v in all_values(await self.get_map_raw(), 12)
                if isinstance(v, bytes)
            ]
            new_room_id = max(ids, default=0) + 1
        body = (
            f_msg(1, point(*start))
            + f_msg(2, point(*end))
            + f_int(3, room_id)
            + f_int(4, new_room_id)
        )
        result = await self.map_operation(f_msg(1, body))
        result["new_room_id"] = new_room_id
        return result

    async def merge_rooms(
        self, room_a: int, room_b: int, keep: int | None = None
    ) -> dict[str, Any]:
        """Merge two adjacent rooms; keep is the surviving id (room_a by default).

        Same format as the app; not exercised on a live map (changes room ids).
        """
        keep = room_a if keep is None else keep
        if keep not in (room_a, room_b):
            raise ValueError("keep must be one of the merged rooms")
        body = f_int(1, room_a) + f_int(2, room_b) + f_int(3, keep)
        return await self.map_operation(f_msg(2, body))

    # --- maps and tasks ---

    async def list_maps(self) -> list[dict[str, Any]]:
        """Saved maps (map/get_all_reduced_maps): id, name, main flag, rooms."""
        resp = await self.send_command("map/get_all_reduced_maps", timeout=15.0)  # type: ignore[attr-defined]
        maps = []
        for value in all_values(resp.raw_payload, 2):
            if not isinstance(value, bytes):
                continue
            name = first(value, 28)
            maps.append(
                {
                    "map_id": first(value, 1),
                    "name": name.decode(errors="replace") if isinstance(name, bytes) else "",
                    "is_main": bool(first(value, 30)),
                    "rooms": len(all_values(value, 12)),
                }
            )
        return maps

    async def switch_map(self, map_id: int) -> CommandResponse:
        """Make another saved map active."""
        return await self.send_command("map/switch_map", f_int(1, map_id), timeout=15.0)  # type: ignore[attr-defined]

    async def delete_map(self, map_id: int) -> CommandResponse:
        """Delete a saved map (irreversible)."""
        return await self.send_command("map/delete_map", f_int(1, map_id), timeout=15.0)  # type: ignore[attr-defined]

    async def start_mapping(self, rebuild_current: bool = False) -> CommandResponse:
        """Start a mapping run: new main map, or rebuild the current one."""
        payload = f_int(1, 1) + (f_int(4, True) if rebuild_current else b"")
        return await self.send_command("map/build_map/start", payload, timeout=15.0)  # type: ignore[attr-defined]

    async def get_continue_task(self) -> dict[str, Any]:
        """Interrupted task the robot can resume, if any."""
        resp = await self.send_command("task/get_continue_task")  # type: ignore[attr-defined]
        raw = resp.raw_payload
        return {
            "available": first(raw, 6) is not None,
            "task_type": first(raw, 4),
            "stopped_at": first(raw, 5),
            "finished_rooms": [v for v in all_values(raw, 9) if isinstance(v, int)],
        }

    async def continue_task(self) -> CommandResponse:
        """Resume the interrupted task."""
        return await self.send_command("task/continue_task", f_int(1, True))  # type: ignore[attr-defined]

    async def check_firmware(self) -> int | None:
        """Firmware update status (CheckFirmware FirmwareStatus enum)."""
        resp = await self.send_command("common/upgrade/check_firmware", timeout=15.0)  # type: ignore[attr-defined]
        value = first(resp.raw_payload, 1)
        return int(value) if isinstance(value, int) else None


def decode_map_objects(map_raw: bytes) -> dict[str, list[dict[str, Any]]]:
    """No-go zones, carpets, thresholds, rooms and furniture of a map payload."""
    zones = []
    for value in all_values(map_raw, 9):
        if isinstance(value, bytes):
            pts = [
                (as_float(first(p, 1)), as_float(first(p, 2)))
                for p in all_values(value, 1)
                if isinstance(p, bytes)
            ]
            zones.append(
                {
                    "id": first(value, 3),
                    "type": first(value, 2),
                    "room_id": first(value, 4),
                    "polygon": pts,
                }
            )
    carpets = []
    for value in all_values(map_raw, 26):
        if isinstance(value, bytes):
            carpets.append(
                {
                    "id": first(value, 1),
                    "option": first(value, 4),
                    "source": first(value, 5),
                    "room_id": first(value, 12),
                    "polygon": [
                        (as_signed(first(g, 1)) or 0, as_signed(first(g, 2)) or 0)
                        for g in all_values(value, 2)
                        if isinstance(g, bytes)
                    ],
                }
            )
    thresholds = []
    for value in all_values(map_raw, 41):
        if isinstance(value, bytes):
            s, e = first(value, 2), first(value, 3)
            thresholds.append(
                {
                    "id": first(value, 1),
                    "start": (
                        (as_float(first(s, 1)), as_float(first(s, 2)))
                        if isinstance(s, bytes)
                        else None
                    ),
                    "end": (
                        (as_float(first(e, 1)), as_float(first(e, 2)))
                        if isinstance(e, bytes)
                        else None
                    ),
                    "height": first(value, 4),
                }
            )
    furniture = []
    vision = first(map_raw, 32)
    for value in all_values(vision if isinstance(vision, bytes) else b"", 1):
        if isinstance(value, bytes):
            furniture.append(
                {
                    "id": as_signed(first(value, 1)),
                    "type": first(value, 2),
                    "angle": as_float(first(value, 4)),
                }
            )
    return {
        "no_go_zones": zones,
        "carpets": carpets,
        "thresholds": thresholds,
        "furniture": furniture,
    }
