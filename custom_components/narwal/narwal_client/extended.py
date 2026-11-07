"""Extended local commands for the Narwal Flow (AX12), verified on firmware v01.08.03.07.

Every command here was exercised against a real robot over the local
WebSocket; formats come from the Narwal app's protobuf definitions. See
docs/local-protocol-extensions.md for the evidence and caveats.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from . import pb

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
    for field, wire, value, _raw in pb.iter_fields(config):
        name = by_get.get(field)
        if name is None or wire != pb.WIRE_VARINT:
            continue
        kind = CONFIG_FIELDS[name].kind
        values[name] = bool(value) if kind == "bool" else int(value)
    return values


def decode_freo_preset(preset: bytes | None) -> dict[FreoStrategy, int | None]:
    """Decode smartCleanParam: each strategy is {1: customized, 2: value}; absent = Smart."""
    result: dict[FreoStrategy, int | None] = {s: None for s in FreoStrategy}
    if not preset:
        return result
    for field, _wire, value, _raw in pb.iter_fields(preset):
        try:
            strategy = FreoStrategy(field)
        except ValueError:
            continue
        if isinstance(value, bytes):
            level = pb.first(value, 2)
            result[strategy] = int(level) if isinstance(level, int) else None
    return result


class ExtendedCommandsMixin:
    """Commands mixed into NarwalClient. Relies on send_command / _send_unawaited."""

    # --- settings (config/get, config/set) ---

    async def get_config_raw(self) -> bytes:
        """Return the raw Config message from config/get."""
        resp = await self.send_command(TOPIC_CMD_CONFIG_GET)  # type: ignore[attr-defined]
        config = pb.first(resp.raw_payload, 2)
        return config if isinstance(config, bytes) else b""

    async def get_config(self) -> dict[str, Any]:
        """Return every known setting by name, plus the Freo preset."""
        raw = await self.get_config_raw()
        values = decode_config(raw)
        preset = pb.first(raw, CONFIG_FIELD_FREO_PRESET)
        values["freo_preset"] = decode_freo_preset(
            preset if isinstance(preset, bytes) else None
        )
        return values

    async def set_config(self, name: str, value: int | bool) -> CommandResponse:
        """Write one setting. SetConfig_Request is a oneof: one field per call."""
        spec = CONFIG_FIELDS[name]
        if spec.kind == "enum" and int(value) not in spec.options:
            raise ValueError(f"{name}: {value} not in {spec.options}")
        if spec.kind == "int" and not 0 <= int(value) <= 100:
            raise ValueError(f"{name}: {value} out of range 0-100")
        payload = pb.f_int(spec.set_field, value)
        return await self.send_command(TOPIC_CMD_CONFIG_SET, payload)  # type: ignore[attr-defined]

    async def set_freo_strategy(
        self, strategy: FreoStrategy, value: int | None
    ) -> CommandResponse:
        """Set one Freo Mind preset strategy; None returns it to Smart (AI).

        The preset is written whole, so the current one is read first and only
        the requested sub-field changes.
        """
        raw = await self.get_config_raw()
        current = pb.first(raw, CONFIG_FIELD_FREO_PRESET)
        preset = current if isinstance(current, bytes) else b""
        entry = (
            pb.f_msg(int(strategy), pb.f_int(1, True) + pb.f_int(2, value))
            if value is not None
            else None
        )
        preset = pb.replace_field(preset, int(strategy), entry)
        payload = pb.f_msg(CONFIG_FIELD_FREO_PRESET, preset)
        return await self.send_command(TOPIC_CMD_CONFIG_SET, payload)  # type: ignore[attr-defined]

    # --- language ---

    async def get_language(self) -> int | None:
        """Robot voice language (Language enum)."""
        resp = await self.send_command(TOPIC_CMD_LANGUAGE_GET)  # type: ignore[attr-defined]
        value = pb.first(resp.raw_payload, 2)
        return int(value) if isinstance(value, int) else None

    async def get_supported_languages(self) -> list[int]:
        """Languages this robot can switch to."""
        resp = await self.send_command(TOPIC_CMD_SUPPORTED_LANGUAGES)  # type: ignore[attr-defined]
        values: list[int] = []
        for value in pb.all_values(resp.raw_payload, 2):
            if isinstance(value, int):
                values.append(value)
            elif isinstance(value, bytes):  # packed
                pos = 0
                while pos < len(value):
                    item, pos = pb.read_varint(value, pos)
                    values.append(item)
        return values

    async def set_language(self, language: int, timeout: float = 60.0) -> bool:
        """Switch the robot voice language.

        The robot answers RESULT_TYPE_FAILED even when it switches (it may
        download a voice pack first), so success is decided by reading back.
        """
        await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_LANGUAGE_SET, pb.f_int(1, language), timeout=timeout
        )
        return await self.get_language() == language

    # --- pets ---

    async def get_pet_voices(self) -> list[PetVoice]:
        """Dog and cat clips stored on the robot (voice/get_voice_list TeasePet)."""
        resp = await self.send_command(  # type: ignore[attr-defined]
            TOPIC_CMD_VOICE_LIST, pb.f_int(1, VOICE_TYPE_TEASE_PET)
        )
        voices: list[PetVoice] = []
        for group in pb.all_values(resp.raw_payload, 1):
            if not isinstance(group, bytes):
                continue
            kind_value = pb.first(group, 2)
            kind = PET_VOICE_KINDS.get(
                int(kind_value) if isinstance(kind_value, int) else 0, "dog"
            )
            for info in pb.all_values(group, 3):
                if not isinstance(info, bytes):
                    continue
                name, path, image = (pb.first(info, n) for n in (1, 2, 3))
                if isinstance(path, bytes):
                    voices.append(
                        PetVoice(
                            kind=kind,
                            name=name.decode(errors="replace") if isinstance(name, bytes) else "",
                            path=path.decode(errors="replace"),
                            image_url=image.decode(errors="replace") if isinstance(image, bytes) else "",
                        )
                    )
        return voices

    async def play_voice_file(self, path: str) -> CommandResponse:
        """Play a clip stored on the robot (developer/play_voice, resourceType FILE)."""
        if not path.startswith("/userdata/"):
            raise ValueError("voice path must be a robot path under /userdata/")
        payload = pb.f_int(4, 2) + pb.f_bytes(5, path)
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
        pose = pb.f_msg(1, pb.point(x_dm, y_dm)) + pb.f_float(2, theta)
        payload = pb.f_int(3, map_id) + pb.f_int(4, 2) + pb.f_msg(5, pose)
        return await self.send_command(TOPIC_CMD_POINT_NAVI, payload, timeout=20.0)  # type: ignore[attr-defined]

    async def stop_go_to_point(self) -> CommandResponse:
        """Stop point navigation."""
        return await self.send_command(TOPIC_CMD_STOP_POINT_NAVI)  # type: ignore[attr-defined]

    async def set_manual_control(self, mode: ManualControlMode | int) -> CommandResponse:
        """Enter/leave remote control. OFF sends the robot back to the dock."""
        return await self.send_command(TOPIC_CMD_MANUAL_CONTROL, pb.f_int(1, int(mode)))  # type: ignore[attr-defined]

    async def send_velocity(self, linear: int, angular: int) -> None:
        """One TelecontrolVelocity sample (publish, no response). App range about ±100."""
        payload = b""
        if linear:
            payload += pb.f_int(1, linear)
        if angular:
            payload += pb.f_int(2, angular)
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
            body += pb.f_packed(1, maintain)
        if replace:
            body += pb.f_packed(2, replace)
        if not body:
            raise ValueError("nothing to reset")
        return await self.send_command(TOPIC_CMD_RESET_CONSUMABLE, pb.f_msg(1, body))  # type: ignore[attr-defined]

    async def get_battery_info(self) -> dict[str, Any]:
        """Battery health (info/battery_info): capacity, cycles, health, temperature..."""
        resp = await self.send_command(TOPIC_CMD_BATTERY_INFO)  # type: ignore[attr-defined]
        raw = resp.raw_payload
        return {
            "full_charge_capacity": pb.first(raw, 1),
            "charge_cycles": pb.first(raw, 2),
            "soc": pb.first(raw, 4),
            "temperature": pb.as_double(pb.first(raw, 5)),
            "voltage": pb.as_double(pb.first(raw, 6)),
            "current": pb.as_double(pb.first(raw, 7)),
            "health": pb.first(raw, 8),
            "rated_capacity": pb.first(raw, 9),
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
    ) -> bytes:
        """CleanTask for one rectangle in grid cells (ZoneOption.zoneType 2)."""
        xa, xb = sorted((x1, x2))
        ya, yb = sorted((y1, y2))
        corners = ((xa, ya), (xb, ya), (xb, yb), (xa, yb))
        bounds = b"".join(pb.f_msg(3, pb.grid_point(x, y)) for x, y in corners)
        bounds += b"".join(pb.f_msg(4, pb.grid_point(x, y)) for x, y in corners)
        zone = pb.f_int(1, 2) + bounds
        # Pass count lives in sweepTime (5), mopTime (6) or sweepMopSyncTime (7)
        pass_fields = {2: (5,), 3: (6,), 4: (7,), 5: (5, 6)}.get(mode, (7,))
        param = pb.f_int(1, mode) + pb.f_int(2, fan) + pb.f_int(4, water)
        param += b"".join(pb.f_int(f, passes) for f in pass_fields)
        if freo:
            param += pb.f_int(9, True)
        item = pb.f_msg(1, zone) + pb.f_msg(2, param) + pb.f_int(3, 1)
        option = pb.f_int(1, True) if freo else b""
        task_type = {2: 1, 3: 2, 4: 4, 5: 3}.get(mode, 4)
        task = (
            pb.f_int(1, map_id)
            + pb.f_msg(2, item)
            + pb.f_msg(3, option)
            + pb.f_int(5, task_type)
        )
        return pb.f_msg(1, task)

    async def clean_zone(self, map_id: int, x1: int, y1: int, x2: int, y2: int, **kw: Any) -> CommandResponse:
        """Clean a rectangle of grid cells (robot must be docked)."""
        payload = self.build_zone_clean_payload(map_id, x1, y1, x2, y2, **kw)
        return await self.send_command("clean/start_clean", payload, timeout=10.0)  # type: ignore[attr-defined]

    # --- map editing (map/operation) ---

    async def get_map_raw(self) -> bytes:
        """Raw StaticMapPayload (map/get_map field 2)."""
        resp = await self.send_command("map/get_map", timeout=15.0)  # type: ignore[attr-defined]
        value = pb.first(resp.raw_payload, 2)
        return value if isinstance(value, bytes) else b""

    @staticmethod
    def map_revision(map_raw: bytes) -> MapRevision:
        """mapId (1), mapVersion (2) and editVersion (33) of a map payload."""
        values = {n: pb.first(map_raw, n) for n in (1, 2, 33)}
        return MapRevision(
            int(values[1] or 0), int(values[2] or 0), int(values[33] or 0)
        )

    async def map_operation(self, operation: bytes) -> dict[str, Any]:
        """Apply one MapOperationPayload; retries once with the editVersion the robot reports."""
        rev = self.map_revision(await self.get_map_raw())
        edit_version = rev.edit_version
        result: dict[str, Any] = {}
        for _ in range(2):
            req = (
                pb.f_int(1, rev.map_id)
                + pb.f_int(2, rev.map_version)
                + pb.f_int(3, edit_version)
                + pb.f_msg(4, operation)
            )
            resp = await self.send_command(TOPIC_CMD_MAP_OPERATION, req, timeout=15.0)  # type: ignore[attr-defined]
            raw = resp.raw_payload
            result = {
                "result": pb.first(raw, 1),
                "edit_version": pb.first(raw, 3),
                "error_code": pb.first(raw, 4),
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
        for value in pb.all_values(map_raw, 12):
            if isinstance(value, bytes) and pb.first(value, 1) == room_id:
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
            info = pb.replace_field(info, 3, pb.f_bytes(3, name) if name else None)
        if floor is not None:
            info = pb.replace_field(info, 4, pb.f_int(4, int(floor)))
        if heavy_dirty is not None:
            info = pb.replace_field(info, 7, pb.f_int(7, True) if heavy_dirty else None)
        return await self.map_operation(pb.f_msg(3, pb.f_msg(1, info)))

    async def add_no_go_zone(
        self,
        polygon: list[tuple[float, float]],
        zone_id: int,
        zone_type: ForbiddenType | int = ForbiddenType.SWEEP_MOP,
    ) -> dict[str, Any]:
        """Add a no-go / no-mop polygon. Points are grid cells as float32 (ints are rejected)."""
        if len(polygon) < 3:
            raise ValueError("polygon needs at least 3 points")
        area = b"".join(pb.f_msg(1, pb.point(x, y)) for x, y in polygon)
        area += pb.f_int(2, int(zone_type)) + pb.f_int(3, zone_id)
        return await self.map_operation(pb.f_msg(4, pb.f_int(1, 1) + pb.f_msg(2, area)))

    async def add_room_ban(
        self, room_id: int, zone_id: int, zone_type: ForbiddenType | int = ForbiddenType.SWEEP_MOP
    ) -> dict[str, Any]:
        """Forbid a whole room (ForbiddenArea mode ROOM)."""
        area = pb.f_int(2, int(zone_type)) + pb.f_int(3, zone_id) + pb.f_int(4, room_id) + pb.f_int(5, 1)
        return await self.map_operation(pb.f_msg(4, pb.f_int(1, 1) + pb.f_msg(2, area)))

    async def remove_no_go_zone(self, zone_id: int) -> dict[str, Any]:
        """Delete a no-go zone or room ban by id."""
        return await self.map_operation(pb.f_msg(4, pb.f_int(1, 2) + pb.f_msg(2, pb.f_int(3, zone_id))))

    async def set_map_name(self, name: str) -> dict[str, Any]:
        """Rename the map ("" clears it)."""
        return await self.map_operation(pb.f_msg(7, pb.f_bytes(1, name)))
