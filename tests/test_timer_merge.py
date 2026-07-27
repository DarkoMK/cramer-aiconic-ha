"""The read-modify-write path for week timers.

The mower has no partial-update command, so every edit resends the whole slot.
Reading the schedule back takes most of a minute, so an edit must build on the
previous edit rather than on the last confirmed read — otherwise setting the
start time silently wipes the days that were set seconds earlier.
"""

import time
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeState:
    """Mirrors MowerState's timer merging without pulling in Home Assistant."""

    week_timers: list[dict[str, Any]] = field(default_factory=list)
    pending_timers: dict[int, dict[str, Any]] = field(default_factory=dict)

    def timer(self, index):
        confirmed = next(
            (t for t in self.week_timers if t.get("index") == index), None
        )
        pending = self.pending_timers.get(index)
        if pending is None:
            return confirmed
        merged = dict(confirmed or {"index": index, "map_index": 0})
        merged.update(pending["values"])
        return merged


def write(state, index, **changes):
    """The merge that coordinator.async_write_timer performs."""
    current = state.timer(index) or {}
    merged = {
        "hour": changes.get("hour", current.get("hour", 0)),
        "minute": changes.get("minute", current.get("minute", 0)),
        "days": list(changes.get("days", current.get("days", []))),
        "duration_minutes": changes.get(
            "duration_minutes", current.get("duration_minutes", 0)
        ),
        "enabled": changes.get("enabled", current.get("enabled", False)),
    }
    merged["start"] = f"{merged['hour']:02d}:{merged['minute']:02d}"
    state.pending_timers[index] = {"values": merged, "at": time.monotonic()}
    return merged


EXISTING = {
    "index": 0,
    "map_index": 0,
    "enabled": True,
    "modes": ["enabled"],
    "start": "00:00",
    "hour": 0,
    "minute": 0,
    "days": ["mon", "wed", "fri"],
    "duration_minutes": 1440,
}


class TestSuccessiveEdits:
    def test_editing_the_start_keeps_the_days(self):
        """The bug seen on the real mower: days were wiped by the next edit."""
        state = FakeState()
        write(state, 3, days=["sat"])
        result = write(state, 3, hour=10, minute=0)
        assert result["days"] == ["sat"]
        assert result["start"] == "10:00"

    def test_three_edits_compose(self):
        state = FakeState()
        write(state, 3, days=["sat", "sun"])
        write(state, 3, hour=9, minute=30)
        result = write(state, 3, duration_minutes=120)
        assert result == {
            "hour": 9,
            "minute": 30,
            "days": ["sat", "sun"],
            "duration_minutes": 120,
            "enabled": False,
            "start": "09:30",
        }

    def test_editing_an_existing_timer_keeps_untouched_fields(self):
        state = FakeState(week_timers=[dict(EXISTING)])
        result = write(state, 0, duration_minutes=600)
        assert result["days"] == ["mon", "wed", "fri"]
        assert result["enabled"] is True
        assert result["duration_minutes"] == 600

    def test_pending_edit_is_visible_immediately(self):
        state = FakeState(week_timers=[dict(EXISTING)])
        write(state, 0, hour=7)
        assert state.timer(0)["hour"] == 7
        # the confirmed copy is untouched until the mower is read back
        assert state.week_timers[0]["hour"] == 0

    def test_days_list_is_copied_not_aliased(self):
        state = FakeState()
        days = ["mon"]
        write(state, 1, days=days)
        days.append("tue")
        assert state.timer(1)["days"] == ["mon"]


class TestPendingExpiry:
    @staticmethod
    def prune(state, read_started):
        state.pending_timers = {
            i: e for i, e in state.pending_timers.items() if e["at"] >= read_started
        }

    def test_read_clears_edits_it_already_covers(self):
        state = FakeState()
        write(state, 3, days=["sat"])
        read_started = time.monotonic() + 1  # read began after the edit
        state.week_timers = [{"index": 3, "days": ["sat"], "hour": 0, "minute": 0,
                              "duration_minutes": 0, "enabled": False}]
        self.prune(state, read_started)
        assert state.pending_timers == {}
        assert state.timer(3)["days"] == ["sat"]

    def test_edit_made_during_a_read_survives(self):
        """A write that lands mid-read must not be dropped by that read."""
        state = FakeState()
        read_started = time.monotonic()
        write(state, 2, days=["sun"], duration_minutes=30)
        state.week_timers = []  # the in-flight read did not see the new slot
        self.prune(state, read_started)
        assert 2 in state.pending_timers
        assert state.timer(2)["days"] == ["sun"]

    def test_empty_slot_reads_as_none(self):
        assert FakeState().timer(2) is None
