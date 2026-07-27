"""Shared bits for the per-slot week-timer entities.

The mower stores its schedule in numbered slots. Each slot is exposed as four
entities — an enable switch, a start time, a duration and the set of weekdays —
so the schedule can be edited from the dashboard and driven by automations
instead of only through a service call.
"""

from __future__ import annotations

from typing import Any

from .const import SCHEDULE_SLOTS
from .coordinator import CramerCoordinator
from .entity import CramerEntity


def slot_range() -> range:
    """Slot indices the integration exposes."""
    return range(SCHEDULE_SLOTS)


class CramerTimerEntity(CramerEntity):
    """Base for an entity bound to one week-timer slot."""

    def __init__(
        self, coordinator: CramerCoordinator, device_id: str, index: int, key: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._index = index
        self._attr_unique_id = f"{device_id}_timer_{index}_{key}"
        self._attr_translation_placeholders = {"slot": str(index + 1)}

    @property
    def timer(self) -> dict[str, Any] | None:
        """The mower's current definition of this slot, if it has one."""
        return self.mower.timer(self._index)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timer = self.timer
        return {
            "slot": self._index,
            "configured": timer is not None,
            "days": timer.get("days") if timer else [],
        }
