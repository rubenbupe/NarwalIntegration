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

## New services

`clean_rooms` gains `freo: true` (Freo Mind with the stored preset); new: `clean_zone`, `go_to_point`, `remote_move`, `play_pet_voice`,
`get_pet_voices` (response), `reset_consumables`, `add_no_go_zone`, `add_room_ban`,
`remove_no_go_zone`, `update_room` (name, floor material, heavily-soiled flag), `set_map_name`.

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
- Not implemented on purpose: room split/merge (changes room ids), No stairs (safety prompt in
  the app), custom button mappings (can disable the physical buttons), video/microphone/photos
  (Narwal cloud + Agora; `developer/take_picture` returns an AES-encrypted image whose key comes
  from the cloud).
