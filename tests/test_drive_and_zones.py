"""Manual drive and zone (map) listing."""

import pytest


class TestDrive:
    def test_body_layout(self, protocol):
        """i16 speed, i16 angular velocity, u16 waypoint handling."""
        _, pid, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_drive_mower(20, -10, protocol.WAYPOINT_NONE))
        )
        assert pid == 510
        assert body == bytes.fromhex("1400f6ff0000")

    def test_negative_speed_reverses(self, protocol):
        import struct

        _, _, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_drive_mower(-35, 0))
        )
        assert struct.unpack_from("<h", body, 0)[0] == -35

    def test_waypoint_flag(self, protocol):
        _, _, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_drive_mower(0, 0, protocol.WAYPOINT_HERE))
        )
        assert int.from_bytes(body[4:6], "little") == 1

    @pytest.mark.parametrize(
        ("speed", "angular"), [(101, 0), (-101, 0), (0, 101), (0, -101)]
    )
    def test_limits_are_enforced(self, protocol, speed, angular):
        with pytest.raises(ValueError):
            protocol.cmd_drive_mower(speed, angular)

    def test_limits_are_inclusive(self, protocol):
        assert protocol.cmd_drive_mower(100, -100)

    def test_decode_response(self, protocol):
        import struct

        body = (
            bytes([0, 1])
            + struct.pack("<i", -1250)
            + struct.pack("<i", 3400)
            + (90).to_bytes(2, "little")
        )
        decoded = protocol.decode_drive(body)
        assert decoded["waypoint_availability"] == "ok_weak_signal"
        assert decoded["relative_east"] == -1250
        assert decoded["relative_north"] == 3400
        assert decoded["orientation"] == 90

    def test_unknown_orientation_is_none(self, protocol):
        body = bytes([0, 0]) + bytes(8) + (0xFFFF).to_bytes(2, "little")
        assert protocol.decode_drive(body)["orientation"] is None

    def test_refusal_reasons_are_named(self, protocol):
        for code, expected in (
            (4, "not_in_map_mode"),
            (5, "manual_control_not_available"),
            (2, "cant_set_no_signal"),
        ):
            body = bytes([0, code]) + bytes(10)
            assert protocol.decode_drive(body)["waypoint_availability"] == expected


class TestZones:
    @staticmethod
    def build(protocol, entries):
        body = bytes([0]) + (0).to_bytes(2, "little")
        for name, code in entries:
            raw = name.encode("iso-8859-15")
            body += raw + b"\x00" * (21 - len(raw)) + code.to_bytes(2, "little")
        return body

    def test_request_carries_site_and_index(self, protocol):
        _, pid, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_get_maps("Garden", 3))
        )
        assert pid == 498
        assert len(body) == 23
        assert body[:6] == b"Garden"
        assert int.from_bytes(body[21:23], "little") == 3

    def test_healthy_zone(self, protocol):
        """A zero status mask means everything is fine — the bits are inverted."""
        decoded = protocol.decode_maps(self.build(protocol, [("FullGarden", 0)]))
        zone = decoded["maps"][0]
        assert zone["name"] == "FullGarden"
        assert zone["confirmed"] is True
        assert zone["charging_station_ok"] is True
        assert zone["working_areas_reachable"] is True
        assert zone["working_area_ok"] is True
        assert zone["verification_ongoing"] is False

    def test_problem_bits_are_inverted(self, protocol):
        decoded = protocol.decode_maps(self.build(protocol, [("Back", 0b10111)]))
        zone = decoded["maps"][0]
        assert zone["confirmed"] is False
        assert zone["charging_station_ok"] is False
        assert zone["working_areas_reachable"] is False
        assert zone["verification_ongoing"] is False
        assert zone["working_area_ok"] is False

    def test_verification_ongoing_is_not_inverted(self, protocol):
        decoded = protocol.decode_maps(self.build(protocol, [("Back", 8)]))
        assert decoded["maps"][0]["verification_ongoing"] is True

    def test_multiple_zones(self, protocol):
        decoded = protocol.decode_maps(
            self.build(protocol, [("Front", 0), ("Back", 0), ("Side", 0)])
        )
        assert [z["name"] for z in decoded["maps"]] == ["Front", "Back", "Side"]

    def test_empty_response(self, protocol):
        assert protocol.decode_maps(bytes([0]))["maps"] == []

    def test_rejects_empty_body(self, protocol):
        with pytest.raises(ValueError, match="too short"):
            protocol.decode_maps(b"")
