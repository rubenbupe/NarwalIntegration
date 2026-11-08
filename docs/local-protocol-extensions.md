# Local protocol extensions (Flow / AX12)

Everything here was exercised against a Narwal Flow (AX12, firmware v01.08.03.07) over the
local WebSocket, using formats taken from the Narwal app's protobuf definitions. Code:
`narwal_client/extended.py` (commands), `narwal_client/pb.py` (exact wire encoding),
`custom_components/narwal/robot_settings*.py`, `extended_services.py`, `button.py`.

## New entities

| Platform | Entities | Source |
|---|---|---|
| switch | 21 robot/dock settings (child lock, plateau mode, pet mode, carpet cleaning, auto-empty family, hot-water wash, disinfection, Freo smart deep clean / intensive cleaning, voice assistant, auto power-off, obstacle photos, dirt/dust-sensor detection, all-object recognition, precious item guard, moisture-proof pad, auto detergent) | `config/get` / `config/set` |
| select | 13 enum settings (mop drying, rewash area, carpet modes, Freo intensity, dock/robot default mode, obstacle avoidance, corner cleaning, pet icon, carpet priority, CarpetFocus, station light), 4 Freo Mind preset selects (re-mop limit, coverage, suction, mop humidity; `smart` = AI), voice language | `config/set` 29, `config/language/*` |
| number | voice volume | `config/set` 1 |
| sensor | battery health, charge cycles, temperature, capacity | `info/battery_info` |
| button | Find pet, End remote control | `pet/search_pet`, `telecontrol/set_manual_control_mode` |

Settings the official app hides are created disabled. Settings poll every 10 minutes and
refresh right after each write.

## Schedules, map management and maps

| Platform | Entities |
|---|---|
| switch | Do not disturb, Off-peak charging, No stairs (writes the global flag and the per-map mirror) |
| time | Do not disturb start / end, Off-peak charging start |
| number | Off-peak charging duration (3–24 h; the robot rejects under 3 h), Corner cleaning interval |
| select | Corner cleaning frequency (off / every N cleans / every N days), Rewash strategy (by area / smart) |
| sensor | Map objects: no-go zones, carpets, thresholds, furniture, room order and saved maps as attributes (ids for the services) |
| binary_sensor | Firmware update ready (`common/upgrade/check_firmware` = DOWNLOADED) |
| button | Continue interrupted task |

## New services

`clean_rooms` gains `freo: true` (Freo Mind with the stored preset); new: `clean_zone`, `go_to_point`, `remote_move`, `play_pet_voice`,
`get_pet_voices` (response), `reset_consumables`, `add_no_go_zone`, `add_room_ban`,
`remove_no_go_zone`, `update_room` (name, floor material, heavily-soiled flag), `set_map_name`,
`set_room_order`, `add_carpet` / `set_carpet_mode` / `delete_carpet`, `add_threshold` /
`delete_threshold` (ids are assigned by the robot and returned), `update_furniture` /
`delete_furniture`, `rotate_map`, `split_room` / `merge_rooms` (app format, not exercised on a
live map), `switch_map`, `delete_map` (needs confirm), `start_mapping`, `continue_task`.

## Protocol facts the code depends on

- `config/set` is a oneof sent **unwrapped**: one field per call. Set and Get field numbers
  differ from field 10 on (`CONFIG_FIELDS` keeps both).
- Freo Mind start = `CleanParam.9 = 1` on every item + `TaskOption.1 = 1`. The preset is
  `config` field 29; each strategy is `{1: true, 2: value}`, absent = Smart. The whole
  message is written, so the current preset is read first.
- `config/language/set` answers `RESULT_TYPE_FAILED` even when it switches; success is read back.
- `telecontrol/point_navi` must carry `3 mapId`, `4 exploreMode`, `5 points[PoseData]`
  (float32 decimetres). Field 1 `poseData` without `mapId` makes the robot drop the socket.
- Remote control: `set_manual_control_mode 1` (JOYSTICK; the robot undocks slightly), stream
  `telecontrol/velocity_control {1 linear, 2 angular}` (±100, ~5 Hz, never answered — listed in
  `UNACKNOWLEDGED_TOPICS`), mode 0 ends it and the robot docks.
- `supply/wash_mop` only works while a task runs (mid-clean wash). Idle washing is
  `supply/wash_mop_by_robot_status` (what the existing dock switch sends) or `supply/wash_and_dry_mop`.
- Pet Finding is `pet/search_pet`; `supply/search_pet` never answers.
- Pet clips: `voice/get_voice_list {1: 1}` → paths under `/userdata/usr/data/voice/`;
  play with `developer/play_voice {4: 2, 5: path}`.
- Map edits (`map/operation`) need the current `editVersion`; on
  `ERROR_CODE_UNMATCHED_EDIT_VERSION` the reply carries it and the call is retried once.
  No-go polygons use float32 cells (int points are rejected); zone-clean bounds use int32 cells.
- Not implemented on purpose: custom button mappings (can disable the physical buttons), video/microphone/photos
  (Narwal cloud + Agora; `developer/take_picture` returns an AES-encrypted image whose key comes
  from the cloud).

## Per-room Freo Mind

Each room has `switch.<room> Freo Mind`, stored with the other room profile fields (restored
across restarts). A Freo room gets `CleanParam.9 = 1`; the task gets `TaskOption.1 = 1` when the
whole task or any room uses Freo. `narwal.clean_rooms` also takes `freo: true` for the whole task.

## Map card (`custom:narwal-map-card`)

Served from `/narwal_static/narwal-map-card.js` and registered automatically on every dashboard
(no Lovelace resource to add). Visual editor included. Options:

| Option | Meaning |
|---|---|
| `entity` | the Narwal vacuum |
| `title` | header text (empty hides the header in picker mode) |
| `tabs` | full card tabs to show: `rooms`, `zones`, `goto`, `nogo`, `remote` |
| `default_tab` | tab opened first |
| `selection: switches` | picker mode: no actions, tapping a room toggles its selection switch |
| `zone_mode_entity` | input_boolean: picker shows a Rooms/Zones toggle bound to it |
| `zones_entity` | input_text: drawn zones stored as JSON `[[x1, y1, x2, y2], ...]` |
| `room_path` | hold a room to open this path (`{key}` = room key, e.g. `/dash/narwal-room-{key}`) |
| `global_entity` | when this boolean is off, each room shows its profile summary |
| `profile_entity` | profile summary sensor template (default `sensor.narwal_{key}_profile`) |
| `rotation` | override the map rotation (0/90/180/270; default: the robot's) |

Picker example (the dashboard's own Start script cleans the selection or the zones):

```yaml
type: custom:narwal-map-card
entity: vacuum.narwal_flow_vacuum
selection: switches
zone_mode_entity: input_boolean.narwal_zone_mode
zones_entity: input_text.narwal_zones
room_path: /main-dashboard/narwal-room-{key}
```

Touch gestures are only captured while drawing (zones, no-go), so pages scroll on phones.

## WebSocket API

| Command | Returns / does |
|---|---|
| `narwal/map` (`entity_id`, `refresh`) | grid (base64, 1 byte per cell: room id, 250 wall, 251 floor), size, `left`/`bottom`, resolution, rooms (names from the vacuum's area mapping), dock, robot, no-go zones, carpets (outlines), thresholds, rotation. Cached 20 s. |
| `narwal/pose` | live robot pose in cells from `map/display_map` (no robot call), route signature, status, docked, battery |
| `narwal/path` | current cleaning route in cells (`null` = gap); the card fetches it when the signature changes (`show_path: false` hides it) |
| `narwal/remote` (`mode`, `linear`, `angular`) | manual control mode and one velocity sample |

Cells are absolute map cells (same frame as `clean_zone`, `add_no_go_zone`, `add_carpet`);
decimetres = cells × resolution / 100. `clean_zone` accepts `zones: [[x1, y1, x2, y2], ...]`.

## Translations

English and Spanish (`translations/es.json`) for every entity and the config flow.
