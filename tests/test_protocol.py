"""Protocol tests against frames captured verbatim from the real mower.

Every frame below was received over AWS IoT MQTT from a Cramer AiConic RLM3
(firmware 8.0.55). Frames that carried a GPS position or a site name have been
regenerated with synthetic values and recomputed CRCs; the rest are stored
exactly as the cloud delivered them.
"""

import importlib.util
from pathlib import Path

import pytest

# protocol.py is deliberately dependency-free, so load it directly rather than
# importing the package (whose __init__ pulls in Home Assistant).
_SPEC = importlib.util.spec_from_file_location(
    "cramer_protocol",
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "cramer_aiconic"
    / "protocol.py",
)
protocol = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(protocol)


# --- captured frames -------------------------------------------------------
FRAME_STATUS_PUSH = bytes.fromhex(
    "851900EA02100002DFD4676A640800005E000000000000065E"
)
FRAME_MOWER_STATUS = bytes.fromhex(
    "411D0051001400000200DFD4676A64730001640800005E38024CED8DE5"
)
FRAME_GNSS = bytes.fromhex("4213005F000A00003880371F907DEA0207C6B4")
FRAME_CUTTING_HEIGHT = bytes.fromhex("430D00D7010400002860017FF9")
FRAME_OPERATION_MODE = bytes.fromhex(
    "443600FD012D0000000047617264656E0000000000000000000000000000004675"
    "6C6C47617264656E00000000000000000000002874"
)

ALL_FRAMES = [
    FRAME_STATUS_PUSH,
    FRAME_MOWER_STATUS,
    FRAME_GNSS,
    FRAME_CUTTING_HEIGHT,
    FRAME_OPERATION_MODE,
]


def body_of(frame: bytes) -> bytes:
    return protocol.parse_frame(frame)[2]


class TestFraming:
    @pytest.mark.parametrize("frame", ALL_FRAMES)
    def test_real_frames_parse_and_verify_crc(self, frame):
        message_id, parameter_id, body = protocol.parse_frame(frame)
        assert 0 <= message_id <= 255
        assert parameter_id in protocol.DATAPOINT_DECODERS
        # header (7) + body + crc (2) must account for the whole frame
        assert len(frame) == 7 + len(body) + 2

    @pytest.mark.parametrize("frame", ALL_FRAMES)
    def test_declared_length_matches_actual(self, frame):
        assert int.from_bytes(frame[1:3], "little") == len(frame)

    def test_rejects_corrupted_frame(self):
        bad = bytearray(FRAME_MOWER_STATUS)
        bad[10] ^= 0xFF
        with pytest.raises(ValueError, match="CRC"):
            protocol.parse_frame(bytes(bad))

    def test_rejects_short_frame(self):
        with pytest.raises(ValueError, match="too short"):
            protocol.parse_frame(b"\x01\x02")

    def test_rejects_truncated_frame(self):
        with pytest.raises(ValueError, match="truncated"):
            protocol.parse_frame(FRAME_MOWER_STATUS[:-4])


class TestBuild:
    def test_get_mower_status_matches_payload_the_mower_accepted(self):
        # This exact hex was POSTed to send_command and answered by the mower.
        assert protocol.cmd_get(protocol.P_GET_MOWER_STATUS, 7) == "0709005000000015f5"

    def test_frame_round_trips(self):
        frame = protocol.build_frame(protocol.P_START_MOWER, b"\x01\x02\x03\x04", 42)
        assert protocol.parse_frame(frame) == (42, protocol.P_START_MOWER, b"\x01\x02\x03\x04")

    def test_start_mower_body_layout(self):
        # u16 overrideSchedule, u16 pin, u8 startOption, u8 mapSettingsIndex
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_start_mower(0xFFFF, 0, 0, 254, 1))
        )
        assert parameter_id == 618
        assert body == bytes.fromhex("ffff000000fe")

    def test_start_mower_can_follow_week_timer(self):
        _, _, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_start_mower(override_schedule=0))
        )
        assert body[:2] == b"\x00\x00"

    def test_park_defaults_to_indefinite(self):
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_park_mower())
        )
        assert parameter_id == 230
        assert body == b"\xff\xff"

    def test_park_with_duration(self):
        _, _, body = protocol.parse_frame(bytes.fromhex(protocol.cmd_park_mower(120)))
        assert int.from_bytes(body, "little") == 120

    def test_pause_body(self):
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_pause_mower())
        )
        assert parameter_id == 226
        assert body == b"\x00\x00"

    def test_cutting_height_body(self):
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_set_cutting_height(45))
        )
        assert parameter_id == 468
        assert body == bytes([45])

    def test_message_id_is_carried(self):
        for mid in (1, 42, 255):
            frame = bytes.fromhex(protocol.cmd_pause_mower(message_id=mid))
            assert protocol.parse_frame(frame)[0] == mid


class TestDecode:
    def test_mower_status(self):
        decoded = protocol.decode_mower_status(body_of(FRAME_MOWER_STATUS))
        assert decoded["main_state"] == "parked"
        assert decoded["battery"] == 100
        assert decoded["signal_quality"] == 100
        assert decoded["next_start_stop"] == 1785189599
        assert decoded["next_start_stop_source"] == "week_timer_map_cut_start"
        assert set(decoded["status_flags"]) == {
            "start_pressed",
            "in_charging_station",
            "enabled",
            "receiving_correction_data",
            "rtk_fix",
        }

    def test_status_push_agrees_with_full_status(self):
        """The two independent state sources must not disagree."""
        full = protocol.decode_mower_status(body_of(FRAME_MOWER_STATUS))
        push = protocol.decode_status_push(body_of(FRAME_STATUS_PUSH))
        for key in (
            "main_state",
            "main_state_code",
            "battery",
            "next_start_stop",
            "next_start_stop_source",
            "configuration_hash",
        ):
            assert push[key] == full[key], key

    def test_gnss_position(self):
        decoded = protocol.decode_gnss(body_of(FRAME_GNSS))
        assert decoded["latitude"] == pytest.approx(52.3731)
        assert decoded["longitude"] == pytest.approx(4.8922)
        assert decoded["hdop"] == 7

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [(52.3731, 4.8922), (-33.8688, 151.2093), (-54.8019, -68.3030), (0.0, 0.0)],
    )
    def test_gnss_handles_both_hemispheres(self, lat, lon):
        """Coordinates are signed int32 at 1e-7; negatives must not wrap."""
        import struct

        body = (
            bytes([0])
            + struct.pack("<ii", round(lat * 1e7), round(lon * 1e7))
            + bytes([3])
        )
        decoded = protocol.decode_gnss(body)
        assert decoded["latitude"] == pytest.approx(lat)
        assert decoded["longitude"] == pytest.approx(lon)

    def test_cutting_height(self):
        decoded = protocol.decode_cutting_height(body_of(FRAME_CUTTING_HEIGHT))
        assert decoded["default_cutting_height"] == 40
        assert decoded["cutting_height"] == 96

    def test_operation_mode_and_names(self):
        decoded = protocol.decode_operation_mode(body_of(FRAME_OPERATION_MODE))
        assert decoded["operation_mode"] == "automatic"
        assert decoded["site_name"] == "Garden"
        assert decoded["map_name"] == "FullGarden"

    def test_unknown_next_start_stop_is_none(self):
        body = bytearray(body_of(FRAME_STATUS_PUSH))
        body[1:5] = (0xFFFFFFFF).to_bytes(4, "little")
        assert protocol.decode_status_push(bytes(body))["next_start_stop"] is None

    def test_decoders_reject_truncated_bodies(self):
        for decoder, frame in (
            (protocol.decode_status_push, FRAME_STATUS_PUSH),
            (protocol.decode_mower_status, FRAME_MOWER_STATUS),
            (protocol.decode_gnss, FRAME_GNSS),
            (protocol.decode_cutting_height, FRAME_CUTTING_HEIGHT),
        ):
            with pytest.raises(ValueError, match="too short"):
                decoder(body_of(frame)[:2])

    def test_every_poll_datapoint_has_a_decoder(self):
        for index in protocol.POLL_DATAPOINTS:
            assert index in protocol.DATAPOINT_DECODERS

    def test_enrich_commands_map_to_poll_datapoints(self):
        for parameter_id in protocol.ENRICH_COMMANDS:
            assert parameter_id + 1 in protocol.POLL_DATAPOINTS

    def test_state_names_are_unique_and_named(self):
        names = list(protocol.MAIN_STATE.values())
        assert len(names) == len(set(names))
        assert all(name and not name.startswith("unknown") for name in names)
