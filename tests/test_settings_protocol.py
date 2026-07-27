"""Tests for the settings that are only reachable over MQTT.

All bodies below were captured from a real mower by subscribing to
``device_response`` while the matching read command was in flight — the cloud
never caches these, so MQTT is the only source. Site names have been replaced
with a synthetic value.
"""

import pytest

FRONT_LIGHT = bytes.fromhex("000301")
REAR_LIGHT = bytes.fromhex("000301")
SOUND = bytes.fromhex("000301")
OBSTACLE = bytes.fromhex("000101")
SPEED = bytes.fromhex("0002ff01")
SELECTED_SITE = bytes.fromhex("0047617264656E000000000000000000000000000000")
SITE_NAMES = bytes.fromhex(
    "00000047617264656E000000000000000000000000000000320000000100"
)
AUTO_UPDATE = bytes.fromhex("00010204")
WIRELESS = bytes.fromhex("0003360101010102010101010101692cf20e")
CLOCK = bytes.fromhex("0028b9676a201c0000")


class TestModeDecoders:
    def test_front_light_is_on(self, protocol):
        assert protocol.decode_front_light(FRONT_LIGHT)["front_light"] == "on"

    def test_rear_light_is_on(self, protocol):
        assert protocol.decode_rear_light(REAR_LIGHT)["rear_light"] == "on"

    def test_sound_is_on(self, protocol):
        assert protocol.decode_sound(SOUND)["sound"] == "on"

    def test_obstacle_handling_is_slow_down(self, protocol):
        assert (
            protocol.decode_obstacle_handling(OBSTACLE)["obstacle_handling"]
            == "slow_down"
        )

    def test_light_modes_differ_only_in_development_code(self, protocol):
        """The front light calls it 5, the rear light calls it 7."""
        assert protocol.FRONT_LIGHT_MODE[5] == "development"
        assert protocol.REAR_LIGHT_MODE[7] == "development"
        assert 7 not in protocol.FRONT_LIGHT_MODE
        assert 5 not in protocol.REAR_LIGHT_MODE

    def test_unknown_code_does_not_raise(self, protocol):
        decoded = protocol.decode_sound(bytes([0, 99]))
        assert decoded["sound"] == "unknown_99"


class TestOtherDecoders:
    def test_default_speed_is_a_predefined_step(self, protocol):
        decoded = protocol.decode_default_speed(SPEED)
        assert decoded["default_speed"] == 2
        assert decoded["default_speed_kind"] == "predefined"

    @pytest.mark.parametrize(
        ("raw", "kind", "value"),
        [
            (0, "predefined", 0),
            (15, "predefined", 15),
            (20, "cm_per_s", 20),
            (150, "cm_per_s", 150),
            (151, "percent", 1),
            (250, "percent", 100),
            (255, "default", None),
        ],
    )
    def test_speed_ranges_match_mapping_speed(self, protocol, raw, kind, value):
        decoded = protocol.decode_default_speed(bytes([0, raw]))
        assert decoded["default_speed_kind"] == kind
        assert decoded["default_speed"] == value

    def test_selected_site(self, protocol):
        assert protocol.decode_selected_site(SELECTED_SITE)["selected_site"] == "Garden"

    def test_site_names(self, protocol):
        assert protocol.decode_site_names(SITE_NAMES)["site_names"] == ["Garden"]

    def test_site_names_handles_an_empty_list(self, protocol):
        assert protocol.decode_site_names(bytes.fromhex("000000"))["site_names"] == []

    def test_auto_update_is_enabled(self, protocol):
        assert protocol.decode_auto_update(AUTO_UPDATE)["auto_update"] is True

    def test_auto_update_disabled(self, protocol):
        assert protocol.decode_auto_update(bytes([0, 0]))["auto_update"] is False

    def test_wireless(self, protocol):
        decoded = protocol.decode_wireless(WIRELESS)
        assert decoded["return_code"] == 0
        assert decoded["lte_signal"] == 0x36
        assert decoded["sim_card_status"] == 1

    def test_clock_carries_the_local_offset(self, protocol):
        decoded = protocol.decode_clock(CLOCK)
        assert decoded["utc_time"] == 1785182504
        # Macedonia in summer is UTC+2.
        assert decoded["timezone_offset"] == 7200


class TestSetterCommands:
    @pytest.mark.parametrize(
        ("builder", "parameter_id", "value", "expected"),
        [
            ("cmd_set_front_light", 606, "off", 2),
            ("cmd_set_front_light", 606, "on", 3),
            ("cmd_set_front_light", 606, "default", 255),
            ("cmd_set_rear_light", 610, "on", 3),
            ("cmd_set_sound", 614, "off", 2),
            ("cmd_set_obstacle_handling", 670, "avoid_objects", 3),
            ("cmd_set_obstacle_handling", 670, "disabled", 0),
        ],
    )
    def test_setters_encode_the_right_code(
        self, protocol, builder, parameter_id, value, expected
    ):
        frame = bytes.fromhex(getattr(protocol, builder)(value))
        _, pid, body = protocol.parse_frame(frame)
        assert pid == parameter_id
        assert body == bytes([expected])

    def test_unknown_option_is_rejected(self, protocol):
        with pytest.raises(ValueError, match="unknown value"):
            protocol.cmd_set_sound("disco")

    def test_auto_update_command_shape(self, protocol):
        """SetAutoUpdateRequest.write emits the flag then two constants."""
        _, pid, body = protocol.parse_frame(bytes.fromhex(protocol.cmd_set_auto_update(True)))
        assert pid == 790
        assert body == bytes([1, 2, 4])
        _, _, off = protocol.parse_frame(bytes.fromhex(protocol.cmd_set_auto_update(False)))
        assert off == bytes([0, 2, 4])

    def test_selected_site_is_nul_padded_to_21_bytes(self, protocol):
        _, pid, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_set_selected_site("Garden"))
        )
        assert pid == 756
        assert len(body) == 21
        assert body.startswith(b"Garden\x00")

    def test_long_site_name_is_truncated_not_overflowed(self, protocol):
        _, _, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_set_selected_site("X" * 60))
        )
        assert len(body) == 21
        assert body.endswith(b"\x00")

    def test_site_names_request_carries_the_start_index(self, protocol):
        _, pid, body = protocol.parse_frame(bytes.fromhex(protocol.cmd_get_site_names(5)))
        assert pid == 732
        assert int.from_bytes(body, "little") == 5


class TestRoundTrip:
    """Every setter's value must survive a decode of the matching getter."""

    @pytest.mark.parametrize(
        ("setter", "decoder", "key", "options"),
        [
            ("cmd_set_front_light", "decode_front_light", "front_light", "FRONT_LIGHT_OPTIONS"),
            ("cmd_set_rear_light", "decode_rear_light", "rear_light", "REAR_LIGHT_OPTIONS"),
            ("cmd_set_sound", "decode_sound", "sound", "SOUND_OPTIONS"),
            (
                "cmd_set_obstacle_handling",
                "decode_obstacle_handling",
                "obstacle_handling",
                "OBSTACLE_OPTIONS",
            ),
        ],
    )
    def test_every_offered_option_round_trips(
        self, protocol, setter, decoder, key, options
    ):
        for option in getattr(protocol, options):
            frame = bytes.fromhex(getattr(protocol, setter)(option))
            _, _, body = protocol.parse_frame(frame)
            # A getter response is the return code followed by the same code.
            decoded = getattr(protocol, decoder)(bytes([0, body[0]]))
            assert decoded[key] == option

    def test_every_mqtt_setting_command_has_a_decoder(self, protocol):
        for parameter_id in protocol.MQTT_SETTING_COMMANDS:
            assert parameter_id + 1 in protocol.MQTT_DECODERS, parameter_id


class TestCoverageAndFirmware:
    """Captured from a parked mower."""

    COVERAGE = bytes.fromhex("0000006d04ffff")
    SW_PACKAGE = bytes.fromhex("00b4000301080037000000")

    def test_map_coverage(self, protocol):
        decoded = protocol.decode_map_coverage(self.COVERAGE)
        assert decoded["area_cut"] == 0
        assert decoded["area_remaining"] == 1133
        # 0xFFFF means the mower has no estimate, not 65535 minutes.
        assert decoded["estimated_remaining_minutes"] is None

    def test_map_coverage_with_a_real_estimate(self, protocol):
        body = bytes.fromhex("00") + (200).to_bytes(2, "little") + \
            (900).to_bytes(2, "little") + (45).to_bytes(2, "little")
        decoded = protocol.decode_map_coverage(body)
        assert decoded["area_cut"] == 200
        assert decoded["area_remaining"] == 900
        assert decoded["estimated_remaining_minutes"] == 45

    def test_firmware_version(self, protocol):
        decoded = protocol.decode_sw_package(self.SW_PACKAGE)
        assert decoded["firmware_version"] == "8.0.55"
        assert decoded["device_group"] == 180

    def test_short_bodies_are_rejected(self, protocol):
        with pytest.raises(ValueError, match="too short"):
            protocol.decode_map_coverage(b"\x00\x01")
        with pytest.raises(ValueError, match="too short"):
            protocol.decode_sw_package(b"\x00\x01")
