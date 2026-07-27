"""Binary payload protocol for Cramer AiConic (Globe RLM) robotic mowers.

Reverse engineered from the Cramer AiConic Android app v1.2.9
(``cramer.aiconic.blelibrary.PayloadBuilder`` and the ``payloads.requests``
package). The same framing is used for BLE and for cloud commands — the cloud
just carries the frame as a lowercase hex string.

Frame layout (little-endian throughout)::

    u8   messageId
    u16  totalLength      (= len(body) + 9)
    u16  parameterId
    u16  bodyLength
    ...  body
    u16  crc16            (over the whole frame with these two bytes zeroed)

A response frame has the same shape with ``parameterId = request + 1`` and the
response body starting at offset 7. The cloud caches the most recent response
body per parameter id and serves it from ``get_datapoints``, which is what this
integration reads.
"""

from __future__ import annotations

import struct
from typing import Any

# --- CRC-16/ARC (poly 0xA001, reflected) with final XOR 0xFFFF --------------
_CRC_TABLE: list[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xA001 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)

# Spot-check against literals recovered from CRC16Kt.crc16Table in the APK.
assert _CRC_TABLE[1] == 49345
assert _CRC_TABLE[2] == 49537
assert _CRC_TABLE[192] == 20480


def crc16(data: bytes) -> int:
    """CRC as computed by ``CRC16Kt.crc16``."""
    crc = 0
    for byte in data:
        crc = ((crc >> 8) ^ _CRC_TABLE[(byte ^ crc) & 0xFF]) & 0xFFFF
    return (crc ^ 0xFFFF) & 0xFFFF


# --- Parameter IDs (from the *Request.PARAMETER_ID constants) --------------
P_GET_MOWER_STATUS = 80
P_GET_GNSS_POSITION = 94
P_GET_WIRELESS_STATUS = 96
P_GET_CLOCK = 156
P_PAUSE_MOWER = 226
P_PARK_MOWER_BY_USER = 230
P_SET_CUTTING_HEIGHT = 468
P_GET_CUTTING_HEIGHT = 470
P_GET_OPERATION_MODE = 508
P_SET_FRONT_LIGHT = 606
P_GET_FRONT_LIGHT = 608
P_SET_REAR_LIGHT = 610
P_GET_REAR_LIGHT = 612
P_SET_SOUND = 614
P_GET_SOUND = 616
P_START_MOWER = 618
P_SET_OBSTACLE_HANDLING = 670
P_GET_OBSTACLE_HANDLING = 672
P_GET_SITE_NAMES = 732
P_SET_DEFAULT_SPEED = 752
P_GET_DEFAULT_SPEED = 754
P_SET_SELECTED_SITE = 756
P_GET_SELECTED_SITE = 758
P_DRIVE_MOWER = 510
P_GET_MAPS = 498
P_GET_MOWER_SW_PACKAGE = 590
P_GET_MAP_COVERAGE = 640
P_SET_WEEK_TIMER = 598
P_GET_ALL_WEEK_TIMERS = 602
P_CLEAR_WEEK_TIMER = 604
P_SET_MULTIPLE_WEEK_TIMERS = 696
P_SET_AUTO_UPDATE = 790
P_GET_AUTO_UPDATE = 792

# The mower pushes an unsolicited status frame on this id roughly every 30 s;
# the cloud caches it, so reading this datapoint needs no command at all.
P_MOWER_STATUS_PUSH = 746

# Response datapoint ids are request id + 1.
DP_STATUS_PUSH = P_MOWER_STATUS_PUSH
DP_MOWER_STATUS = P_GET_MOWER_STATUS + 1  # 81
DP_GNSS = P_GET_GNSS_POSITION + 1  # 95
DP_CUTTING_HEIGHT = P_GET_CUTTING_HEIGHT + 1  # 471
DP_OPERATION_MODE = P_GET_OPERATION_MODE + 1  # 509

#: Datapoints read on every poll cycle.
POLL_DATAPOINTS = [
    DP_STATUS_PUSH,
    DP_MOWER_STATUS,
    DP_GNSS,
    DP_CUTTING_HEIGHT,
    DP_OPERATION_MODE,
]

#: Read-only commands issued periodically to refresh the slow datapoints.
ENRICH_COMMANDS = [
    P_GET_MOWER_STATUS,
    P_GET_CUTTING_HEIGHT,
    P_GET_OPERATION_MODE,
]

# Response ids for the settings the cloud does NOT cache. These are only
# readable by listening on MQTT while the request is in flight.
DP_WIRELESS = P_GET_WIRELESS_STATUS + 1        # 97
DP_CLOCK = P_GET_CLOCK + 1                     # 157
DP_FRONT_LIGHT = P_GET_FRONT_LIGHT + 1         # 609
DP_REAR_LIGHT = P_GET_REAR_LIGHT + 1           # 613
DP_SOUND = P_GET_SOUND + 1                     # 617
DP_OBSTACLE_HANDLING = P_GET_OBSTACLE_HANDLING + 1  # 673
DP_SITE_NAMES = P_GET_SITE_NAMES + 1           # 733
DP_DEFAULT_SPEED = P_GET_DEFAULT_SPEED + 1     # 755
DP_SELECTED_SITE = P_GET_SELECTED_SITE + 1     # 759
DP_AUTO_UPDATE = P_GET_AUTO_UPDATE + 1         # 793
DP_WEEK_TIMERS = P_GET_ALL_WEEK_TIMERS + 1     # 603
DP_SW_PACKAGE = P_GET_MOWER_SW_PACKAGE + 1     # 591
DP_MAP_COVERAGE = P_GET_MAP_COVERAGE + 1       # 641
DP_MAPS = P_GET_MAPS + 1                       # 499
DP_DRIVE = P_DRIVE_MOWER + 1                   # 511

#: Settings read over MQTT. The mower drops requests that arrive faster than
#: roughly one every three seconds, so these are paced.
MQTT_SETTING_COMMANDS = [
    P_GET_FRONT_LIGHT,
    P_GET_REAR_LIGHT,
    P_GET_SOUND,
    P_GET_OBSTACLE_HANDLING,
    P_GET_DEFAULT_SPEED,
    P_GET_SELECTED_SITE,
    P_GET_AUTO_UPDATE,
    P_GET_WIRELESS_STATUS,
    P_GET_GNSS_POSITION,
    P_GET_ALL_WEEK_TIMERS,
    P_GET_MAP_COVERAGE,
    P_GET_MOWER_SW_PACKAGE,
    P_GET_MAPS,
]


# --- Enumerations ----------------------------------------------------------
#: ``SignedMainState`` — the mower's primary state.
MAIN_STATE: dict[int, str] = {
    0xFF: "disconnected",
    0: "power_up",
    1: "idle",
    2: "parked",
    3: "paused",
    4: "cutting",
    5: "leaving",
    6: "searching",
    7: "charging",
    8: "error",
    9: "fatal_error",
    10: "recovery",
    11: "alarm",
    12: "secondary_area",
    13: "spot_cutting",
    14: "transportation",
    15: "mapping",
    16: "verification",
    17: "waiting",
    18: "power_down",
}

#: ``GetMowerStatusResponse.StatusFlags`` bit mask.
STATUS_FLAGS: dict[int, str] = {
    1: "start_pressed",
    2: "in_charging_station",
    4: "upside_down",
    8: "demo_mode",
    16: "enabled",
    32: "receiving_correction_data",
    64: "rtk_fix",
}

#: ``StartStopSource`` — what scheduled the next start/stop.
START_STOP_SOURCE: dict[int, str] = {
    0xFE: "unknown",
    0xFF: "init",
    0: "stopped_outside_cs_start",
    1: "charging_state_start",
    2: "week_timer_start",
    3: "max_working_area_start",
    4: "parked_by_user_start",
    5: "rain_indication_start",
    6: "frost_indication_start",
    7: "energy_saving_mode_start",
    8: "week_timer_map_cut_start",
    9: "stopped_outside_cs_charging_needed_start",
    51: "week_timer_stop",
    52: "countdown_timer_stop",
    53: "countdown_week_timer_stop",
}

#: ``GetOperationModeResponse.OperationMode``.
OPERATION_MODE: dict[int, str] = {
    0xFFFF: "init",
    0: "automatic",
    1: "waiting",
    2: "map",
    3: "verification_whole_map",
    4: "transportation",
    5: "verification_not_confirmed_map",
    6: "verification_specified_map",
}

#: ``FrontLightMode`` / ``RearLightMode`` / ``SpeakerSound`` wire codes.
#: The two light modes differ only in their "development" code (5 vs 7).
FRONT_LIGHT_MODE: dict[int, str] = {2: "off", 3: "on", 5: "development", 255: "default"}
REAR_LIGHT_MODE: dict[int, str] = {2: "off", 3: "on", 7: "development", 255: "default"}
SOUND_MODE: dict[int, str] = {2: "off", 3: "on", 4: "development", 255: "default"}

#: ``CameraRadarObstacleHandling``.
OBSTACLE_HANDLING: dict[int, str] = {
    0: "disabled",
    1: "slow_down",
    2: "stop_for_objects",
    3: "avoid_objects",
    255: "default",
}

#: Modes offered in the UI (the development modes are firmware test modes).
FRONT_LIGHT_OPTIONS = ["off", "on", "default"]
REAR_LIGHT_OPTIONS = ["off", "on", "default"]
SOUND_OPTIONS = ["off", "on", "default"]
OBSTACLE_OPTIONS = ["disabled", "slow_down", "stop_for_objects", "avoid_objects", "default"]


def code_for(mapping: dict[int, str], name: str) -> int:
    """Reverse-look-up a wire code from its name."""
    for code, label in mapping.items():
        if label == name:
            return code
    raise ValueError(f"unknown value {name!r}")


#: ``WeekTimer.Days`` bit mask, Monday first.
WEEK_DAYS: dict[int, str] = {
    1: "mon",
    2: "tue",
    4: "wed",
    8: "thu",
    16: "fri",
    32: "sat",
    64: "sun",
}
DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

#: ``WeekTimer.TimerMode`` bit mask.
TIMER_MODE: dict[int, str] = {
    1: "enabled",
    2: "defines_operation_time",
    4: "entire_time",
    8: "restart_cutting",
}

#: A cleared timer slot.
TIMER_INDEX_ALL = 0xFF
MAX_TIMER_INDEX = 99


#: ``DriveMowerRequest`` waypoint handling.
WAYPOINT_NONE = 0
WAYPOINT_HERE = 1

#: ``DriveMowerResponse.WaypointAvailability`` — why a drive was refused.
WAYPOINT_AVAILABILITY: dict[int, str] = {
    0xFF: "init",
    0: "ok_good_signal",
    1: "ok_weak_signal",
    2: "cant_set_no_signal",
    3: "cant_set_calibration_not_ready",
    4: "not_in_map_mode",
    5: "manual_control_not_available",
    6: "self_intersections_not_allowed",
}

#: ``DriveMowerResponse.MOWER_ORIENTATION_UNKNOWN``.
ORIENTATION_UNKNOWN = 0xFFFF

#: Manual drive is a joystick in the app: it streams commands and the mower
#: coasts to a stop when they stop arriving. These bounds keep a single
#: service call from being violent.
DRIVE_SPEED_LIMIT = 100
DRIVE_ANGULAR_LIMIT = 100


#: ``nextStartStop`` sentinel values meaning "no schedule known".
NEXT_START_STOP_UNKNOWN = {0, 0xFFFE, 0xFFFF, 0xFFFFFFFF}

#: States in which the mower is actively working away from the dock.
ACTIVE_STATES = {"cutting", "leaving", "secondary_area", "spot_cutting", "mapping"}
#: States that mean the mower is at/returning to the charging station.
DOCKING_STATES = {"searching"}
ERROR_STATES = {"error", "fatal_error", "alarm"}


# --- Frame building --------------------------------------------------------
def build_frame(parameter_id: int, body: bytes = b"", message_id: int = 1) -> bytes:
    """Build a request frame."""
    out = bytearray()
    out.append(message_id & 0xFF)
    out += ((len(body) + 9) & 0xFFFF).to_bytes(2, "little")
    out += (parameter_id & 0xFFFF).to_bytes(2, "little")
    out += (len(body) & 0xFFFF).to_bytes(2, "little")
    out += body
    out += b"\x00\x00"
    out[-2:] = crc16(bytes(out)).to_bytes(2, "little")
    return bytes(out)


def build_hex(parameter_id: int, body: bytes = b"", message_id: int = 1) -> str:
    """Build a request frame as the lowercase hex string the cloud expects."""
    return build_frame(parameter_id, body, message_id).hex()


def cmd_get(parameter_id: int, message_id: int = 1) -> str:
    """Build a no-argument read command."""
    return build_hex(parameter_id, b"", message_id)


def cmd_start_mower(
    override_schedule: int = 0xFFFF,
    pin: int = 0,
    start_option: int = 0,
    map_settings_index: int = 254,
    message_id: int = 1,
) -> str:
    """Start mowing.

    ``override_schedule`` 0xFFFF cuts the entire map ignoring the week timer,
    0 follows the week timer. ``map_settings_index`` 254 means "same as last
    time", 255 means "use default".
    """
    body = (
        (override_schedule & 0xFFFF).to_bytes(2, "little")
        + (pin & 0xFFFF).to_bytes(2, "little")
        + bytes([start_option & 0xFF, map_settings_index & 0xFF])
    )
    return build_hex(P_START_MOWER, body, message_id)


def cmd_pause_mower(message_id: int = 1) -> str:
    """Pause the mower where it stands."""
    return build_hex(P_PAUSE_MOWER, (0).to_bytes(2, "little"), message_id)


def cmd_park_mower(park_minutes: int = 0xFFFF, message_id: int = 1) -> str:
    """Send the mower back to the charging station.

    ``park_minutes`` 0xFFFF parks until the next scheduled start.
    """
    return build_hex(
        P_PARK_MOWER_BY_USER, (park_minutes & 0xFFFF).to_bytes(2, "little"), message_id
    )


def cmd_set_cutting_height(height_mm: int, message_id: int = 1) -> str:
    """Set the cutting height in millimetres (valid range 20-102)."""
    return build_hex(P_SET_CUTTING_HEIGHT, bytes([height_mm & 0xFF]), message_id)


def cmd_set_front_light(mode: str, message_id: int = 1) -> str:
    """Set the front light mode."""
    return build_hex(P_SET_FRONT_LIGHT, bytes([code_for(FRONT_LIGHT_MODE, mode)]), message_id)


def cmd_set_rear_light(mode: str, message_id: int = 1) -> str:
    """Set the rear light mode."""
    return build_hex(P_SET_REAR_LIGHT, bytes([code_for(REAR_LIGHT_MODE, mode)]), message_id)


def cmd_set_sound(mode: str, message_id: int = 1) -> str:
    """Set the speaker sound mode."""
    return build_hex(P_SET_SOUND, bytes([code_for(SOUND_MODE, mode)]), message_id)


def cmd_set_obstacle_handling(mode: str, message_id: int = 1) -> str:
    """Set how the camera/radar reacts to obstacles."""
    return build_hex(
        P_SET_OBSTACLE_HANDLING, bytes([code_for(OBSTACLE_HANDLING, mode)]), message_id
    )


def cmd_set_default_speed(value: int, message_id: int = 1) -> str:
    """Set the default mowing speed (see ``decode_default_speed`` for units)."""
    return build_hex(P_SET_DEFAULT_SPEED, bytes([value & 0xFF]), message_id)


def cmd_set_selected_site(site_name: str, message_id: int = 1) -> str:
    """Select which mapped site the mower works on."""
    return build_hex(P_SET_SELECTED_SITE, _padded_string(site_name, 21), message_id)


def cmd_set_auto_update(enabled: bool, message_id: int = 1) -> str:
    """Enable or disable automatic firmware updates.

    ``SetAutoUpdateRequest.write`` emits the flag followed by two constants.
    """
    return build_hex(P_SET_AUTO_UPDATE, bytes([1 if enabled else 0, 2, 4]), message_id)


def days_to_mask(days: list[str]) -> int:
    """Turn day names into the ``WeekTimer.Days`` mask."""
    lookup = {name: bit for bit, name in WEEK_DAYS.items()}
    mask = 0
    for day in days:
        key = day.strip().lower()[:3]
        if key not in lookup:
            raise ValueError(f"unknown day {day!r}")
        mask |= lookup[key]
    return mask


def mask_to_days(mask: int) -> list[str]:
    """Day names in week order, so the list reads naturally."""
    present = {name for bit, name in WEEK_DAYS.items() if mask & bit}
    return [day for day in DAY_ORDER if day in present]


def cmd_get_week_timers(site_name: str, map_name: str, message_id: int = 1) -> str:
    """Read every week timer defined for one site/map pair."""
    body = _padded_string(site_name, 21) + _padded_string(map_name, 21)
    return build_hex(P_GET_ALL_WEEK_TIMERS, body, message_id)


def cmd_set_week_timer(
    timer_index: int,
    site_name: str,
    map_name: str,
    hour: int,
    minute: int,
    days: list[str],
    duration_minutes: int,
    *,
    enabled: bool = True,
    defines_operation_time: bool = True,
    entire_time: bool = False,
    restart_cutting: bool = False,
    message_id: int = 1,
) -> str:
    """Write one week timer slot.

    Mirrors ``SetWeekTimerRequest.write``: index, site, map, a reserved zero,
    the mode mask, start time, day mask and the run length in minutes.
    """
    if not 0 <= timer_index <= MAX_TIMER_INDEX:
        raise ValueError(f"timer index must be 0-{MAX_TIMER_INDEX}")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("start time out of range")
    if not 0 <= duration_minutes <= 0xFFFF:
        raise ValueError("duration out of range")

    modes = 0
    if enabled:
        modes |= 1
    if defines_operation_time:
        modes |= 2
    if entire_time:
        modes |= 4
    if restart_cutting:
        modes |= 8

    body = (
        bytes([timer_index])
        + _padded_string(site_name, 21)
        + _padded_string(map_name, 21)
        + bytes([0, modes, hour, minute, days_to_mask(days)])
        + (duration_minutes & 0xFFFF).to_bytes(2, "little")
    )
    return build_hex(P_SET_WEEK_TIMER, body, message_id)


def cmd_clear_week_timer(timer_index: int, message_id: int = 1) -> str:
    """Clear one timer slot, or every slot with ``TIMER_INDEX_ALL``."""
    if timer_index != TIMER_INDEX_ALL and not 0 <= timer_index <= MAX_TIMER_INDEX:
        raise ValueError(f"timer index must be 0-{MAX_TIMER_INDEX} or 0xFF for all")
    return build_hex(P_CLEAR_WEEK_TIMER, bytes([timer_index & 0xFF]), message_id)


def cmd_drive_mower(
    speed: int,
    angular_velocity: int,
    waypoint: int = WAYPOINT_NONE,
    message_id: int = 1,
) -> str:
    """Drive the mower manually for one command step.

    ``speed`` and ``angular_velocity`` are signed; negative reverses. The mower
    only honours this in mapping/manual mode and stops once commands stop
    arriving, so a single call is a nudge rather than a journey.
    """
    if abs(speed) > DRIVE_SPEED_LIMIT:
        raise ValueError(f"speed must be within +/-{DRIVE_SPEED_LIMIT}")
    if abs(angular_velocity) > DRIVE_ANGULAR_LIMIT:
        raise ValueError(f"angular velocity must be within +/-{DRIVE_ANGULAR_LIMIT}")
    body = (
        struct.pack("<h", speed)
        + struct.pack("<h", angular_velocity)
        + (waypoint & 0xFFFF).to_bytes(2, "little")
    )
    return build_hex(P_DRIVE_MOWER, body, message_id)


def cmd_get_maps(site_name: str, first_index: int = 0, message_id: int = 1) -> str:
    """List the maps (zones) defined for a site."""
    body = _padded_string(site_name, 21) + (first_index & 0xFFFF).to_bytes(2, "little")
    return build_hex(P_GET_MAPS, body, message_id)


def cmd_get_site_names(first_index: int = 0, message_id: int = 1) -> str:
    """List the mapped sites, starting at ``first_index``."""
    return build_hex(
        P_GET_SITE_NAMES, (first_index & 0xFFFF).to_bytes(2, "little"), message_id
    )


def _padded_string(value: str, length: int) -> bytes:
    raw = value.encode("iso-8859-15", errors="replace")[: length - 1]
    return raw + b"\x00" * (length - len(raw))


# --- Frame / body parsing --------------------------------------------------
def parse_frame(frame: bytes) -> tuple[int, int, bytes]:
    """Parse and CRC-verify a full frame. Returns (messageId, parameterId, body)."""
    if len(frame) < 9:
        raise ValueError(f"frame too short: {len(frame)} bytes")
    message_id = frame[0]
    total = int.from_bytes(frame[1:3], "little")
    parameter_id = int.from_bytes(frame[3:5], "little")
    body_len = int.from_bytes(frame[5:7], "little")
    if total > len(frame):
        raise ValueError(f"truncated frame: declared {total}, got {len(frame)}")
    buf = bytearray(frame[:total])
    got = int.from_bytes(buf[total - 2 : total], "little")
    buf[-1] = 0
    buf[-2] = 0
    want = crc16(bytes(buf))
    if got != want:
        raise ValueError(f"CRC mismatch: got {got:#06x}, want {want:#06x}")
    return message_id, parameter_id, frame[7 : 7 + body_len]


def _flags(mask: int) -> list[str]:
    return [name for bit, name in STATUS_FLAGS.items() if mask & bit]


def _next_start_stop(value: int) -> int | None:
    return None if value in NEXT_START_STOP_UNKNOWN else value


def decode_status_push(body: bytes) -> dict[str, Any]:
    """Decode the unsolicited status frame (datapoint 746, 16 bytes).

    Layout from ``SetMowerStatusRequest.SetMowerStatusResponse.fromByteStream``.
    """
    if len(body) < 10:
        raise ValueError(f"status push too short: {len(body)}")
    state_code = body[0]
    out: dict[str, Any] = {
        "main_state_code": state_code,
        "main_state": MAIN_STATE.get(state_code, f"unknown_{state_code}"),
        "next_start_stop": _next_start_stop(int.from_bytes(body[1:5], "little")),
        "battery": body[5],
        "next_start_stop_source_code": body[6],
        "next_start_stop_source": START_STOP_SOURCE.get(body[6], f"unknown_{body[6]}"),
        "backend_notification": int.from_bytes(body[7:9], "little"),
        "configuration_hash": body[9],
    }
    if len(body) >= 16:
        out["event_id"] = int.from_bytes(body[10:12], "little")
        out["event_timestamp"] = int.from_bytes(body[12:16], "little") or None
    return out


def decode_mower_status(body: bytes) -> dict[str, Any]:
    """Decode ``GetMowerStatusResponse`` (datapoint 81, >=15 bytes)."""
    if len(body) < 15:
        raise ValueError(f"mower status too short: {len(body)}")
    state_code = body[1]
    mask = int.from_bytes(body[8:10], "little")
    out: dict[str, Any] = {
        "return_code": body[0],
        "main_state_code": state_code,
        "main_state": MAIN_STATE.get(state_code, f"unknown_{state_code}"),
        "sub_state": body[2],
        "next_start_stop": _next_start_stop(int.from_bytes(body[3:7], "little")),
        "battery": body[7],
        "status_flags_mask": mask,
        "status_flags": _flags(mask),
        "wireless_status": body[10],
        "signal_quality": body[11],
        "next_start_stop_source_code": body[12],
        "next_start_stop_source": START_STOP_SOURCE.get(body[12], f"unknown_{body[12]}"),
        "backend_notification": int.from_bytes(body[13:15], "little"),
    }
    if len(body) >= 16:
        out["configuration_hash"] = body[15]
    return out


def decode_gnss(body: bytes) -> dict[str, Any]:
    """Decode ``GetGNSSPositionResponse`` (datapoint 95, 10 bytes)."""
    if len(body) < 10:
        raise ValueError(f"gnss too short: {len(body)}")
    lat, lon = struct.unpack_from("<ii", body, 1)
    return {
        "return_code": body[0],
        "latitude": lat / 1e7,
        "longitude": lon / 1e7,
        "hdop": body[9],
    }


def decode_cutting_height(body: bytes) -> dict[str, Any]:
    """Decode ``GetCuttingHeightResponse`` (datapoint 471, 4 bytes)."""
    if len(body) < 4:
        raise ValueError(f"cutting height too short: {len(body)}")
    return {
        "return_code": body[0],
        "default_cutting_height": body[1],
        "cutting_height": body[2],
        "cutting_height_information": body[3],
    }


def _nul_string(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("iso-8859-15", errors="replace")


def decode_operation_mode(body: bytes) -> dict[str, Any]:
    """Decode ``GetOperationModeResponse`` (datapoint 509, 45 bytes)."""
    if len(body) < 3:
        raise ValueError(f"operation mode too short: {len(body)}")
    mode = int.from_bytes(body[1:3], "little")
    return {
        "return_code": body[0],
        "operation_mode_code": mode,
        "operation_mode": OPERATION_MODE.get(mode, f"unknown_{mode}"),
        "site_name": _nul_string(body[3:24]) if len(body) >= 24 else None,
        "map_name": _nul_string(body[24:45]) if len(body) >= 45 else None,
    }


def _mode_decoder(mapping: dict[int, str], key: str):
    """Build a decoder for the ``u8 returnCode, u8 value`` responses."""

    def decode(body: bytes) -> dict[str, Any]:
        if len(body) < 2:
            raise ValueError(f"{key} response too short: {len(body)}")
        return {
            "return_code": body[0],
            key: mapping.get(body[1], f"unknown_{body[1]}"),
            f"{key}_code": body[1],
        }

    return decode


decode_front_light = _mode_decoder(FRONT_LIGHT_MODE, "front_light")
decode_rear_light = _mode_decoder(REAR_LIGHT_MODE, "rear_light")
decode_sound = _mode_decoder(SOUND_MODE, "sound")
decode_obstacle_handling = _mode_decoder(OBSTACLE_HANDLING, "obstacle_handling")


def decode_default_speed(body: bytes) -> dict[str, Any]:
    """Decode ``GetDefaultSpeedResponse`` (datapoint 755).

    ``MappingSpeed.fromUInt8``: 0-15 is a predefined step, 20-150 is cm/s,
    151-250 is a percentage offset by 150, 255 means "mower default".
    """
    if len(body) < 2:
        raise ValueError(f"default speed response too short: {len(body)}")
    raw = body[1]
    if raw < 16:
        kind, value = "predefined", raw
    elif 20 <= raw < 151:
        kind, value = "cm_per_s", raw
    elif 151 <= raw < 251:
        kind, value = "percent", raw - 150
    else:
        kind, value = "default", None
    return {
        "return_code": body[0],
        "default_speed_raw": raw,
        "default_speed_kind": kind,
        "default_speed": value,
    }


def decode_selected_site(body: bytes) -> dict[str, Any]:
    """Decode ``GetSelectedSiteResponse`` (datapoint 759)."""
    if len(body) < 2:
        raise ValueError(f"selected site response too short: {len(body)}")
    return {
        "return_code": body[0],
        "selected_site": _nul_string(body[1:22]) or None,
    }


def decode_site_names(body: bytes) -> dict[str, Any]:
    """Decode ``GetSiteNamesResponse`` (datapoint 733).

    After the return code and a u16 index comes a repeating record of
    21-byte name, u32 timestamp and u16 identifier.
    """
    if len(body) < 3:
        raise ValueError(f"site names response too short: {len(body)}")
    names: list[str] = []
    offset = 3
    while offset + 27 <= len(body):
        name = _nul_string(body[offset : offset + 21])
        if name:
            names.append(name)
        offset += 27
    return {"return_code": body[0], "site_names": names}


def decode_week_timers(body: bytes) -> dict[str, Any]:
    """Decode ``GetAllWeekTimersResponse`` (datapoint 603).

    A return code followed by any number of 8-byte records:
    index, map index, mode mask, hour, minute, day mask, u16 length.
    """
    if not body:
        raise ValueError("week timer response too short: 0")
    timers: list[dict[str, Any]] = []
    offset = 1
    while offset + 8 <= len(body):
        index, map_index, modes, hour, minute, days = body[offset : offset + 6]
        length = int.from_bytes(body[offset + 6 : offset + 8], "little")
        timers.append(
            {
                "index": index,
                "map_index": map_index,
                "enabled": bool(modes & 1),
                "modes": [name for bit, name in TIMER_MODE.items() if modes & bit],
                "start": f"{hour:02d}:{minute:02d}",
                "hour": hour,
                "minute": minute,
                "days": mask_to_days(days),
                "duration_minutes": length,
            }
        )
        offset += 8
    return {"return_code": body[0], "week_timers": timers}


#: ``GetMapCoverageResponse.estimatedRemainingTime`` sentinel for "no estimate".
COVERAGE_TIME_UNKNOWN = 0xFFFF


def decode_map_coverage(body: bytes) -> dict[str, Any]:
    """Decode ``GetMapCoverageResponse`` (datapoint 641).

    Areas are square metres; the remaining time is minutes, with 0xFFFF
    meaning the mower cannot estimate it yet.
    """
    if len(body) < 7:
        raise ValueError(f"map coverage response too short: {len(body)}")
    remaining_time = int.from_bytes(body[5:7], "little")
    return {
        "return_code": body[0],
        "area_cut": int.from_bytes(body[1:3], "little"),
        "area_remaining": int.from_bytes(body[3:5], "little"),
        "estimated_remaining_minutes": (
            None if remaining_time == COVERAGE_TIME_UNKNOWN else remaining_time
        ),
    }


def decode_sw_package(body: bytes) -> dict[str, Any]:
    """Decode ``GetMowerSWPackageResponse`` (datapoint 591)."""
    if len(body) < 11:
        raise ValueError(f"sw package response too short: {len(body)}")
    return {
        "return_code": body[0],
        "device_group": int.from_bytes(body[1:3], "little"),
        "sub_device": body[3],
        "variant": body[4],
        "firmware_version": f"{body[5]}.{body[6]}.{int.from_bytes(body[7:11], 'little')}",
    }


def decode_drive(body: bytes) -> dict[str, Any]:
    """Decode ``DriveMowerResponse`` (datapoint 511)."""
    if len(body) < 12:
        raise ValueError(f"drive response too short: {len(body)}")
    availability = body[1]
    orientation = int.from_bytes(body[10:12], "little")
    return {
        "return_code": body[0],
        "waypoint_availability": WAYPOINT_AVAILABILITY.get(
            availability, f"unknown_{availability}"
        ),
        "relative_east": struct.unpack_from("<i", body, 2)[0],
        "relative_north": struct.unpack_from("<i", body, 6)[0],
        "orientation": None if orientation == ORIENTATION_UNKNOWN else orientation,
    }


def decode_maps(body: bytes) -> dict[str, Any]:
    """Decode ``GetMapsResponse`` (datapoint 499).

    A return code, a u16 continuation index, then 23-byte records of a
    21-byte name and a u16 status mask. In that mask a set bit means *not*
    ok, which is why the flags below are inverted.
    """
    if not body:
        raise ValueError("maps response too short: 0")
    if len(body) < 3:
        return {"return_code": body[0], "next_index": 0, "maps": []}

    maps: list[dict[str, Any]] = []
    offset = 3
    while offset + 23 <= len(body):
        name = _nul_string(body[offset : offset + 21])
        code = int.from_bytes(body[offset + 21 : offset + 23], "little")
        if name:
            maps.append(
                {
                    "name": name,
                    "confirmed": not code & 1,
                    "charging_station_ok": not code & 2,
                    "working_areas_reachable": not code & 4,
                    "verification_ongoing": bool(code & 8),
                    "working_area_ok": not code & 16,
                    "status_code": code,
                }
            )
        offset += 23
    return {
        "return_code": body[0],
        "next_index": int.from_bytes(body[1:3], "little"),
        "maps": maps,
    }


def decode_auto_update(body: bytes) -> dict[str, Any]:
    """Decode ``AutoUpdateResponse`` (datapoint 793)."""
    if len(body) < 2:
        raise ValueError(f"auto update response too short: {len(body)}")
    return {"return_code": body[0], "auto_update": bool(body[1])}


def decode_wireless(body: bytes) -> dict[str, Any]:
    """Decode ``GetMowerWirelessCommunicationStatusResponse`` (datapoint 97)."""
    if len(body) < 14:
        raise ValueError(f"wireless response too short: {len(body)}")
    return {
        "return_code": body[0],
        "lte_hw_status": body[1],
        "lte_signal": body[2],
        "gnss_hw_status": body[3],
        "sim_card_status": body[4],
        "ble_hw_status": body[5],
        "wireless_connection_status": body[6],
        "wifi_hw_status": body[9],
        "lora_hw_status": body[11],
        "rtk_hw_status": body[12],
        "rtk_connection_status": body[13],
    }


def decode_clock(body: bytes) -> dict[str, Any]:
    """Decode ``GetClockResponse`` (datapoint 157)."""
    if len(body) < 9:
        raise ValueError(f"clock response too short: {len(body)}")
    return {
        "return_code": body[0],
        "utc_time": int.from_bytes(body[1:5], "little"),
        "timezone_offset": struct.unpack_from("<i", body, 5)[0],
    }


#: Decoders for the settings that are only reachable over MQTT.
MQTT_DECODERS = {
    DP_FRONT_LIGHT: decode_front_light,
    DP_REAR_LIGHT: decode_rear_light,
    DP_SOUND: decode_sound,
    DP_OBSTACLE_HANDLING: decode_obstacle_handling,
    DP_DEFAULT_SPEED: decode_default_speed,
    DP_SELECTED_SITE: decode_selected_site,
    DP_SITE_NAMES: decode_site_names,
    DP_AUTO_UPDATE: decode_auto_update,
    DP_WIRELESS: decode_wireless,
    DP_CLOCK: decode_clock,
    DP_WEEK_TIMERS: decode_week_timers,
    DP_MAP_COVERAGE: decode_map_coverage,
    DP_SW_PACKAGE: decode_sw_package,
    DP_MAPS: decode_maps,
    DP_DRIVE: decode_drive,
    DP_GNSS: decode_gnss,
    DP_MOWER_STATUS: decode_mower_status,
    DP_STATUS_PUSH: decode_status_push,
    DP_CUTTING_HEIGHT: decode_cutting_height,
    DP_OPERATION_MODE: decode_operation_mode,
}


#: Datapoint id -> decoder for the cached response body.
DATAPOINT_DECODERS = {
    DP_STATUS_PUSH: decode_status_push,
    DP_MOWER_STATUS: decode_mower_status,
    DP_GNSS: decode_gnss,
    DP_CUTTING_HEIGHT: decode_cutting_height,
    DP_OPERATION_MODE: decode_operation_mode,
}
