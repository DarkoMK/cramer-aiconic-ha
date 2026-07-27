"""Week-timer (schedule) protocol tests.

The response body below was read from a real mower: one enabled timer running
Mon/Wed/Fri from 00:00 for 1440 minutes.
"""

import pytest

WEEK_TIMERS = bytes.fromhex("00000003000015a005")


class TestDecode:
    def test_matches_the_mower_schedule(self, protocol):
        decoded = protocol.decode_week_timers(WEEK_TIMERS)
        assert decoded["return_code"] == 0
        assert len(decoded["week_timers"]) == 1
        timer = decoded["week_timers"][0]
        assert timer["index"] == 0
        assert timer["start"] == "00:00"
        assert timer["duration_minutes"] == 1440
        assert timer["days"] == ["mon", "wed", "fri"]
        assert timer["enabled"] is True
        assert "defines_operation_time" in timer["modes"]

    def test_days_come_back_in_week_order(self, protocol):
        # Bits are set Sunday-first in the mask; the output must not be.
        assert protocol.mask_to_days(0b1111111) == protocol.DAY_ORDER
        assert protocol.mask_to_days(0b1000001) == ["mon", "sun"]

    def test_empty_schedule(self, protocol):
        assert protocol.decode_week_timers(bytes([0]))["week_timers"] == []

    def test_multiple_timers(self, protocol):
        body = WEEK_TIMERS + bytes.fromhex("0100010800400f00")
        timers = protocol.decode_week_timers(body)["week_timers"]
        assert len(timers) == 2
        assert timers[1]["index"] == 1
        assert timers[1]["start"] == "08:00"
        assert timers[1]["days"] == ["sun"]
        assert timers[1]["duration_minutes"] == 15

    def test_rejects_empty_body(self, protocol):
        with pytest.raises(ValueError, match="too short"):
            protocol.decode_week_timers(b"")


class TestDayMask:
    @pytest.mark.parametrize(
        ("days", "mask"),
        [
            (["mon"], 1),
            (["sun"], 64),
            (["mon", "wed", "fri"], 21),
            (["mon", "tue", "wed", "thu", "fri", "sat", "sun"], 127),
            ([], 0),
        ],
    )
    def test_days_to_mask(self, protocol, days, mask):
        assert protocol.days_to_mask(days) == mask

    def test_accepts_full_day_names(self, protocol):
        assert protocol.days_to_mask(["Monday", "FRIDAY"]) == 1 | 16

    def test_rejects_nonsense(self, protocol):
        with pytest.raises(ValueError, match="unknown day"):
            protocol.days_to_mask(["funday"])

    def test_round_trip(self, protocol):
        for mask in range(128):
            assert protocol.days_to_mask(protocol.mask_to_days(mask)) == mask


class TestCommands:
    def test_get_matches_the_payload_the_mower_answered(self, protocol):
        built = protocol.cmd_get_week_timers("Garden", "FullGarden", 0x11)
        # Header the mower accepted: message id, length 0x5a, parameter 602,
        # body length 42.
        assert built.startswith("1133005a022a00")
        _, pid, body = protocol.parse_frame(bytes.fromhex(built))
        assert pid == 602
        assert len(body) == 42
        assert body[:6] == b"Garden"
        assert body[21:31] == b"FullGarden"

    def test_set_week_timer_body_layout(self, protocol):
        built = protocol.cmd_set_week_timer(
            0, "Garden", "FullGarden", 9, 30, ["mon", "fri"], 240
        )
        _, pid, body = protocol.parse_frame(bytes.fromhex(built))
        assert pid == 598
        # index + site(21) + map(21) + reserved + modes + hh + mm + days + u16
        assert len(body) == 1 + 21 + 21 + 5 + 2
        assert body[0] == 0
        assert body[43] == 0  # reserved
        assert body[44] == 0b11  # enabled | defines_operation_time
        assert body[45] == 9
        assert body[46] == 30
        assert body[47] == 1 | 16
        assert int.from_bytes(body[48:50], "little") == 240

    def test_disabled_timer_clears_the_enabled_bit(self, protocol):
        _, _, body = protocol.parse_frame(
            bytes.fromhex(
                protocol.cmd_set_week_timer(
                    1, "S", "M", 6, 0, ["sat"], 60, enabled=False
                )
            )
        )
        assert body[44] & 1 == 0

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"timer_index": 100}, "timer index"),
            ({"hour": 24}, "start time"),
            ({"minute": 60}, "start time"),
            ({"duration_minutes": 70000}, "duration"),
        ],
    )
    def test_rejects_out_of_range_values(self, protocol, kwargs, match):
        args = {
            "timer_index": 0,
            "site_name": "S",
            "map_name": "M",
            "hour": 9,
            "minute": 0,
            "days": ["mon"],
            "duration_minutes": 60,
        }
        args.update(kwargs)
        with pytest.raises(ValueError, match=match):
            protocol.cmd_set_week_timer(**args)

    def test_clear_one_timer(self, protocol):
        _, pid, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_clear_week_timer(3))
        )
        assert pid == 604
        assert body == bytes([3])

    def test_clear_all_timers(self, protocol):
        _, _, body = protocol.parse_frame(
            bytes.fromhex(protocol.cmd_clear_week_timer(protocol.TIMER_INDEX_ALL))
        )
        assert body == bytes([0xFF])

    def test_clear_rejects_a_bad_index(self, protocol):
        with pytest.raises(ValueError, match="timer index"):
            protocol.cmd_clear_week_timer(150)

    def test_set_then_decode_round_trips(self, protocol):
        """What we write must read back the same through the getter decoder."""
        built = protocol.cmd_set_week_timer(
            2, "Garden", "FullGarden", 14, 45, ["tue", "thu", "sun"], 180
        )
        _, _, body = protocol.parse_frame(bytes.fromhex(built))
        # Rebuild a getter-shaped record from the setter body.
        record = bytes([body[0], 0, body[44], body[45], body[46], body[47]]) + body[48:50]
        timer = protocol.decode_week_timers(bytes([0]) + record)["week_timers"][0]
        assert timer["index"] == 2
        assert timer["start"] == "14:45"
        assert timer["days"] == ["tue", "thu", "sun"]
        assert timer["duration_minutes"] == 180
        assert timer["enabled"] is True
