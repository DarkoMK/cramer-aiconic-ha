"""Tests that exercise the real integration inside Home Assistant.

The rest of the suite tests the protocol in isolation. This file drives the
actual coordinator, config entry and entities through an HA test harness, so
the behaviour being asserted is the behaviour that ships — not a
reimplementation of it in a fake.
"""

from dataclasses import replace
from datetime import time as dt_time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cramer_aiconic import protocol
from custom_components.cramer_aiconic.api import (
    CramerApiError,
    CramerDevice,
    CramerRateLimitError,
)
from custom_components.cramer_aiconic.const import (
    CONF_ENABLE_SETTINGS,
    CONF_REFRESH_TOKEN,
    CONF_REGION_CODE,
    DOMAIN,
)

DEVICE_ID = "1000000000000000001"
PRODUCT_ID = "PRODUCTID0001"

DEVICE = CramerDevice(
    device_id=DEVICE_ID,
    product_id=PRODUCT_ID,
    name="LawnMower",
    model="RLM3",
    serial_number="000000001",
    mac="RLM3_000000001",
    product_type="RLM3",
    is_online=True,
)

# Real frames: a parked mower at 100%, and its cutting height / operation mode.
STATUS_PUSH = "02DFD4676A640800005E000000000000"
MOWER_STATUS = "000200DFD4676A64730001640800005E38024CED"
CUTTING_HEIGHT = "00286001"
OPERATION_MODE = (
    "00000047617264656E0000000000000000000000000000004675"
    "6C6C47617264656E0000000000000000000000"
)
TS = "2026-07-27T20:00:00.000Z"


def week_timer_writes(api) -> list[str]:
    """Only the SetWeekTimer frames — the coordinator also sends read commands."""
    out = []
    for payload in api.commands:
        _, parameter_id, _ = protocol.parse_frame(bytes.fromhex(payload))
        if parameter_id == protocol.P_SET_WEEK_TIMER:
            out.append(payload)
    return out


def datapoints():
    return {
        protocol.DP_STATUS_PUSH: (STATUS_PUSH, TS),
        protocol.DP_MOWER_STATUS: (MOWER_STATUS, TS),
        protocol.DP_CUTTING_HEIGHT: (CUTTING_HEIGHT, TS),
        protocol.DP_OPERATION_MODE: (OPERATION_MODE, TS),
    }


class FakeApi:
    """Stands in for the cloud, recording what the integration asked for."""

    def __init__(self):
        self.refresh_token = "refresh-1"
        self.commands: list[str] = []
        self.device_list_calls = 0
        self.device_list_error: Exception | None = None
        self.devices = [DEVICE]
        self.on_tokens_updated = None
        self.mqtt_info_calls = 0
        #: Timestamp the cloud stamps on its cached datapoints. None means
        #: "the mower reported just now", which is what a healthy mower does
        #: every ~30 s. Tests pin an older value to simulate one that has
        #: stopped reporting.
        self.timestamp: str | None = None

    async def async_get_devices(self):
        self.device_list_calls += 1
        if self.device_list_error is not None:
            raise self.device_list_error
        return self.devices

    def go_offline(self):
        """What the cloud does once the mower stops checking in."""
        self.devices = [replace(DEVICE, is_online=False)]

    def go_online(self):
        self.devices = [DEVICE]

    async def async_get_datapoints(self, product_id, device_id, indices):
        stamp = self.timestamp or dt_util.utcnow().isoformat()
        return {
            index: (data, stamp)
            for index, (data, _) in datapoints().items()
            if index in indices
        }

    async def async_send_command(self, product_id, device_id, payload_hex):
        self.commands.append(payload_hex)
        return True

    async def async_last_known_info(self, product_id, device_id):
        return {"latitude": "52.3731", "longitude": "4.8922"}

    async def async_get_mqtt_info(self):
        self.mqtt_info_calls += 1
        raise CramerApiError("no mqtt in tests")

    async def async_get_aws_credentials(self, mqtt_info):
        raise CramerApiError("no mqtt in tests")

    async def async_ensure_token(self, force=False):
        return "token"


@pytest.fixture
def fake_api():
    return FakeApi()


@pytest.fixture
def config_entry():
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cramer AiConic (test)",
        unique_id="user-1",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_REGION_CODE: "eu-central-1-CONSUMER",
            CONF_REFRESH_TOKEN: "refresh-0",
        },
        # The MQTT settings pass needs a live AWS session; off for these tests.
        options={CONF_ENABLE_SETTINGS: False},
    )


@pytest.fixture
def settings_entry():
    """Settings enabled, so the schedule/select/switch entities exist.

    The MQTT read itself fails against FakeApi, which is fine — it is
    best-effort and must never break setup.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cramer AiConic (test)",
        unique_id="user-1",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_REGION_CODE: "eu-central-1-CONSUMER",
            CONF_REFRESH_TOKEN: "refresh-0",
        },
        options={CONF_ENABLE_SETTINGS: True},
    )


@pytest.fixture
async def setup_with_settings(hass: HomeAssistant, settings_entry, fake_api):
    settings_entry.add_to_hass(hass)
    with patch(
        "custom_components.cramer_aiconic.CramerAiConicApi", return_value=fake_api
    ):
        assert await hass.config_entries.async_setup(settings_entry.entry_id)
        await hass.async_block_till_done()
    return settings_entry


@pytest.fixture
async def setup_integration(hass: HomeAssistant, config_entry, fake_api):
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.cramer_aiconic.CramerAiConicApi", return_value=fake_api
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_post_command_delay():
    """The settle and pacing delays exist to give the cloud time; skip them."""
    with (
        patch(
            "custom_components.cramer_aiconic.coordinator.POST_COMMAND_REFRESH_DELAY", 0
        ),
        patch("custom_components.cramer_aiconic.coordinator.COMMAND_PACING", 0),
    ):
        yield


class TestSetup:
    async def test_entry_loads_and_creates_entities(
        self, hass: HomeAssistant, setup_integration
    ):
        assert setup_integration.state is ConfigEntryState.LOADED
        assert hass.states.get("lawn_mower.lawnmower").state == "docked"
        assert hass.states.get("sensor.lawnmower_battery").state == "100"
        assert hass.states.get("sensor.lawnmower_state").state == "parked"

    async def test_decoded_detail_reaches_the_entities(
        self, hass: HomeAssistant, setup_integration
    ):
        assert hass.states.get("sensor.lawnmower_site").state == "Garden"
        assert hass.states.get("sensor.lawnmower_map").state == "FullGarden"
        assert hass.states.get("sensor.lawnmower_cutting_height").state == "96"
        assert (
            hass.states.get("binary_sensor.lawnmower_in_charging_station").state == "on"
        )

    async def test_unload(self, hass: HomeAssistant, setup_integration):
        assert await hass.config_entries.async_unload(setup_integration.entry_id)
        await hass.async_block_till_done()
        assert setup_integration.state is ConfigEntryState.NOT_LOADED


class TestDeviceListResilience:
    """A throttled device list must not take the whole poll down."""

    async def test_cached_list_survives_a_failed_refresh(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = setup_integration.runtime_data
        assert fake_api.device_list_calls == 1

        # Force the next cycle to be a device-list refresh, and make it fail.
        coordinator._cycle = 0
        fake_api.device_list_error = CramerRateLimitError("service unavailable")

        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert hass.states.get("sensor.lawnmower_battery").state == "100"

    async def test_first_load_failure_is_still_fatal(
        self, hass: HomeAssistant, config_entry, fake_api
    ):
        """With nothing cached there is nothing to report, so it must fail."""
        fake_api.device_list_error = CramerApiError("cloud down")
        config_entry.add_to_hass(hass)
        with patch(
            "custom_components.cramer_aiconic.CramerAiConicApi", return_value=fake_api
        ):
            await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.SETUP_RETRY

    async def test_datapoint_failure_does_fail_the_poll(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = setup_integration.runtime_data
        with patch.object(
            fake_api,
            "async_get_datapoints",
            AsyncMock(side_effect=CramerApiError("boom")),
        ):
            await coordinator.async_refresh()
        assert coordinator.last_update_success is False


class TestEntityAvailability:
    """Entities ride out a short run of failures instead of flapping."""

    async def test_available_through_two_failures(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = setup_integration.runtime_data
        with patch.object(
            fake_api,
            "async_get_datapoints",
            AsyncMock(side_effect=CramerApiError("boom")),
        ):
            await coordinator.async_refresh()
            await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.lawnmower_battery").state == "100"

    async def test_unavailable_once_failures_persist(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        """HA stops notifying listeners after the first failure.

        Without an explicit nudge at the tolerance limit the entities never
        re-evaluate, and the window silently becomes "available forever with
        stale data".
        """
        coordinator = setup_integration.runtime_data
        with patch.object(
            fake_api,
            "async_get_datapoints",
            AsyncMock(side_effect=CramerApiError("boom")),
        ):
            for _ in range(5):
                await coordinator.async_refresh()
                await hass.async_block_till_done()
        assert coordinator.consecutive_failures == 5
        assert hass.states.get("sensor.lawnmower_battery").state == "unavailable"

    async def test_recovers_after_the_cloud_comes_back(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = setup_integration.runtime_data
        with patch.object(
            fake_api,
            "async_get_datapoints",
            AsyncMock(side_effect=CramerApiError("boom")),
        ):
            for _ in range(5):
                await coordinator.async_refresh()
                await hass.async_block_till_done()
        assert hass.states.get("sensor.lawnmower_battery").state == "unavailable"

        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert coordinator.consecutive_failures == 0
        assert hass.states.get("sensor.lawnmower_battery").state == "100"


class TestCommands:
    async def test_dock_sends_the_park_frame(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        await hass.services.async_call(
            "lawn_mower",
            "dock",
            {"entity_id": "lawn_mower.lawnmower"},
            blocking=True,
        )
        assert fake_api.commands
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(fake_api.commands[-1])
        )
        assert parameter_id == protocol.P_PARK_MOWER_BY_USER
        assert body == b"\xff\xff"

    async def test_start_sends_the_start_frame(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        await hass.services.async_call(
            "lawn_mower",
            "start_mowing",
            {"entity_id": "lawn_mower.lawnmower"},
            blocking=True,
        )
        _, parameter_id, _ = protocol.parse_frame(bytes.fromhex(fake_api.commands[-1]))
        assert parameter_id == protocol.P_START_MOWER

    async def test_cutting_height_sends_millimetres(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.lawnmower_cutting_height", "value": 45},
            blocking=True,
        )
        _, parameter_id, body = protocol.parse_frame(
            bytes.fromhex(fake_api.commands[-1])
        )
        assert parameter_id == protocol.P_SET_CUTTING_HEIGHT
        assert body == bytes([45])

    async def test_cutting_height_shows_the_default_it_writes(
        self, hass: HomeAssistant, setup_integration
    ):
        """The control writes the *default* height, so it must read that back.

        Parameter 468 is ``SetDefaultCuttingHeight``; datapoint 471 carries the
        default in byte 1 and the blade's current position in byte 2. Reading
        byte 2 back made the slider ignore what was just written — the fixture
        has a default of 40 and a current height of 96.
        """
        assert hass.states.get("number.lawnmower_cutting_height").state == "40"

    async def test_cutting_height_is_read_back_from_the_mower_after_a_write(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        """A write alone never refreshes the cloud's cached copy.

        The cloud re-caches datapoint 471 only when the mower answers a read of
        parameter 470, so without an explicit read-back the next poll serves the
        pre-write value and the new height looks like it was rejected.
        """
        before = len(fake_api.commands)

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.lawnmower_cutting_height", "value": 45},
            blocking=True,
        )
        await hass.async_block_till_done()

        sent = [
            protocol.parse_frame(bytes.fromhex(payload))[1]
            for payload in fake_api.commands[before:]
        ]
        assert protocol.P_SET_CUTTING_HEIGHT in sent
        assert protocol.P_GET_CUTTING_HEIGHT in sent, (
            "the mower was never asked to re-report the height, so the poll "
            "that follows reads a stale cached datapoint"
        )
        assert sent.index(protocol.P_GET_CUTTING_HEIGHT) > sent.index(
            protocol.P_SET_CUTTING_HEIGHT
        ), "the read-back must follow the write"


class TestScheduleDrafts:
    """Filling a blank slot must not leave a junk timer on the mower."""

    @staticmethod
    def coordinator_of(entry):
        return entry.runtime_data

    async def test_partial_edit_is_not_sent(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = self.coordinator_of(setup_integration)

        await coordinator.async_write_timer(DEVICE_ID, 1, days=["sat"])

        assert not week_timer_writes(fake_api), "an incomplete slot was written"
        # ...but the draft is visible straight away.
        assert coordinator.data[DEVICE_ID].timer(1)["days"] == ["sat"]

    async def test_slot_is_written_once_it_is_complete(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = self.coordinator_of(setup_integration)
        await coordinator.async_write_timer(DEVICE_ID, 1, days=["sat"])
        await coordinator.async_write_timer(DEVICE_ID, 1, hour=9, minute=30)
        assert not week_timer_writes(fake_api)

        await coordinator.async_write_timer(DEVICE_ID, 1, duration_minutes=120)

        writes = week_timer_writes(fake_api)
        assert writes, "a complete slot was never written"
        _, parameter_id, body = protocol.parse_frame(bytes.fromhex(writes[-1]))
        assert parameter_id == protocol.P_SET_WEEK_TIMER
        assert body[45] == 9  # hour
        assert body[46] == 30  # minute
        assert body[47] == protocol.days_to_mask(["sat"])
        assert int.from_bytes(body[48:50], "little") == 120

    async def test_edits_compose_instead_of_overwriting(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        """The regression that wiped the days when the start time was set."""
        coordinator = self.coordinator_of(setup_integration)
        await coordinator.async_write_timer(
            DEVICE_ID, 2, days=["sat", "sun"], duration_minutes=60
        )
        await coordinator.async_write_timer(DEVICE_ID, 2, hour=11, minute=15)

        timer = coordinator.data[DEVICE_ID].timer(2)
        assert timer["days"] == ["sat", "sun"]
        assert timer["duration_minutes"] == 60
        assert timer["start"] == "11:15"

    async def test_editing_a_slot_the_mower_already_has_writes_immediately(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = self.coordinator_of(setup_integration)
        state = coordinator.data[DEVICE_ID]
        state.week_timers = [
            {
                "index": 0,
                "map_index": 0,
                "enabled": True,
                "modes": ["enabled"],
                "start": "00:00",
                "hour": 0,
                "minute": 0,
                "days": ["mon"],
                "duration_minutes": 1440,
            }
        ]

        await coordinator.async_write_timer(DEVICE_ID, 0, hour=7)

        assert week_timer_writes(
            fake_api
        ), "an existing slot should be written straight away"

    async def test_clearing_a_slot_drops_the_draft(
        self, hass: HomeAssistant, setup_integration
    ):
        coordinator = self.coordinator_of(setup_integration)
        await coordinator.async_write_timer(DEVICE_ID, 3, days=["fri"])
        assert coordinator.data[DEVICE_ID].timer(3) is not None

        coordinator.forget_timer_draft(DEVICE_ID, 3)

        assert coordinator.data[DEVICE_ID].timer(3) is None

    async def test_time_entity_edits_go_through_the_draft(
        self, hass: HomeAssistant, setup_with_settings, fake_api
    ):
        await hass.services.async_call(
            "time",
            "set_value",
            {"entity_id": "time.lawnmower_timer_1_start", "time": dt_time(8, 45)},
            blocking=True,
        )
        coordinator = setup_with_settings.runtime_data
        assert coordinator.data[DEVICE_ID].timer(0)["hour"] == 8
        assert not week_timer_writes(fake_api), "an incomplete slot was written"


class TestPendingExpiry:
    """A settings read confirms sent edits but must not eat unsent drafts."""

    async def test_read_clears_sent_edits_only(
        self, hass: HomeAssistant, setup_integration
    ):
        coordinator = setup_integration.runtime_data
        state = coordinator.data[DEVICE_ID]

        # A complete edit (sent) and a draft (not sent).
        await coordinator.async_write_timer(
            DEVICE_ID, 0, days=["mon"], duration_minutes=60
        )
        await coordinator.async_write_timer(DEVICE_ID, 1, days=["tue"])
        assert state.pending_timers[0]["sent"] is True
        assert state.pending_timers[1]["sent"] is False

        # A read that started after those edits confirms the sent one.
        state.settings_read_started = state.pending_timers[0]["at"] + 1
        coordinator._apply_settings(
            state, {protocol.DP_WEEK_TIMERS: bytes.fromhex("00")}
        )

        assert 0 not in state.pending_timers, "a confirmed edit should be dropped"
        assert 1 in state.pending_timers, "an unsent draft must survive a read"

    async def test_settings_timestamp_is_per_mower(
        self, hass: HomeAssistant, setup_integration
    ):
        """Two mowers read independently; one must not expire the other's edits."""
        coordinator = setup_integration.runtime_data
        state = coordinator.data[DEVICE_ID]
        assert hasattr(state, "settings_read_started")
        assert not hasattr(coordinator, "_settings_read_started")


class TestTokenPersistence:
    async def test_rotated_refresh_token_is_written_to_the_entry(
        self, hass: HomeAssistant, config_entry, fake_api
    ):
        """The rotated token must reach disk; it is single-use."""
        captured = {}

        def make_api(*args, **kwargs):
            captured["on_tokens_updated"] = kwargs.get("on_tokens_updated")
            return fake_api

        config_entry.add_to_hass(hass)
        with patch(
            "custom_components.cramer_aiconic.CramerAiConicApi", side_effect=make_api
        ):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()

        await captured["on_tokens_updated"]("refresh-99", "eu-central-1-CONSUMER")
        await hass.async_block_till_done()

        assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-99"

    async def test_persisting_a_token_does_not_reload_the_entry(
        self, hass: HomeAssistant, config_entry, fake_api
    ):
        """Reloading on every rotation made every entity flap to unavailable."""
        captured = {}

        def make_api(*args, **kwargs):
            captured["on_tokens_updated"] = kwargs.get("on_tokens_updated")
            return fake_api

        config_entry.add_to_hass(hass)
        with patch(
            "custom_components.cramer_aiconic.CramerAiConicApi", side_effect=make_api
        ):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()
            first = config_entry.runtime_data

            await captured["on_tokens_updated"]("refresh-100", None)
            await hass.async_block_till_done()

            assert config_entry.runtime_data is first, "the entry was reloaded"

    async def test_changing_options_does_reload(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        first = setup_integration.runtime_data
        with patch(
            "custom_components.cramer_aiconic.CramerAiConicApi", return_value=fake_api
        ):
            hass.config_entries.async_update_entry(
                setup_integration, options={CONF_ENABLE_SETTINGS: False, "scan_interval": 45}
            )
            await hass.async_block_till_done()
        assert setup_integration.runtime_data is not first


class TestOfflineAndStaleness:
    """A mower that stops reporting must look different from a healthy one.

    The cloud serves the last datapoints it ever received, indefinitely and
    without complaint. On 2026-07-31 the real mower flattened its battery
    away from the dock at 07:24; six hours later every entity still read as
    though it were live, the lawn mower entity still said "returning", and
    the only clue was a warning buried in the log every 15 minutes.
    """

    @pytest.fixture(autouse=True)
    def refresh_device_list_every_cycle(self):
        """The online flag rides on the device list, refreshed every 10th poll."""
        with patch(
            "custom_components.cramer_aiconic.coordinator.ENRICH_EVERY_CYCLES", 1
        ):
            yield

    async def test_contact_age_counts_from_the_cloud_timestamp(
        self, hass: HomeAssistant, setup_integration, fake_api, freezer
    ):
        """Age comes from the mower's own timestamp, not from HA's clock.

        A restart resets every ``last_changed`` in HA, so an age derived from
        one silently restarts its count too. This one survives a restart.

        The cloud keeps answering with the same cached timestamp — that is
        precisely what it did for six hours after the mower died — so the age
        has to come from the clock moving on, not the data changing.
        """
        fake_api.timestamp = dt_util.utcnow().isoformat()
        freezer.tick(timedelta(minutes=42))
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("sensor.lawnmower_last_contact_age").state == "42"

    async def test_fresh_while_the_mower_is_reporting(
        self, hass: HomeAssistant, setup_integration
    ):
        assert hass.states.get("sensor.lawnmower_last_contact_age").state == "0"
        assert hass.states.get("lawn_mower.lawnmower").state == "docked"
        assert (
            hass.states.get("sensor.lawnmower_battery").attributes["stale"] is False
        )

    async def test_readings_survive_the_mower_going_offline(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        """The last known values stay visible — they are still the best guess."""
        fake_api.go_offline()
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("sensor.lawnmower_battery").state == "100"

    async def test_readings_are_flagged_stale_when_the_mower_goes_offline(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        fake_api.go_offline()
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("sensor.lawnmower_battery").attributes["stale"] is True

    async def test_the_mower_entity_goes_unavailable_when_the_mower_goes_offline(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        """The one entity that claims to say what the mower is doing.

        Leaving it on its last activity is what made a dead mower read as
        "returning" for six hours.
        """
        fake_api.go_offline()
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("lawn_mower.lawnmower").state == "unavailable"

    async def test_stale_when_the_datapoints_stop_advancing(
        self, hass: HomeAssistant, setup_integration, fake_api, freezer
    ):
        """Belt and braces: the cloud can keep claiming a dead mower is online.

        ``is_online`` stays True here — only the data stops moving.
        """
        fake_api.timestamp = dt_util.utcnow().isoformat()
        freezer.tick(timedelta(minutes=30))
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("lawn_mower.lawnmower").state == "unavailable"
        assert hass.states.get("sensor.lawnmower_battery").attributes["stale"] is True

    async def test_a_two_minute_gap_is_not_stale(
        self, hass: HomeAssistant, setup_integration, fake_api, freezer
    ):
        """Measured over five days: 9629 samples, longest real gap 2.0 min."""
        fake_api.timestamp = dt_util.utcnow().isoformat()
        freezer.tick(timedelta(minutes=2))
        await setup_integration.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("lawn_mower.lawnmower").state == "docked"

    async def test_recovers_when_the_mower_comes_back(
        self, hass: HomeAssistant, setup_integration, fake_api
    ):
        coordinator = setup_integration.runtime_data
        fake_api.go_offline()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("lawn_mower.lawnmower").state == "unavailable"

        fake_api.go_online()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get("lawn_mower.lawnmower").state == "docked"
        assert hass.states.get("sensor.lawnmower_battery").attributes["stale"] is False

    async def test_settings_pass_is_skipped_while_the_mower_is_offline(
        self, hass: HomeAssistant, setup_with_settings, fake_api
    ):
        """An offline mower cannot answer, so asking only spams the log.

        The real one logged a warning every 15.5 minutes for six hours.
        """
        coordinator = setup_with_settings.runtime_data
        fake_api.go_offline()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        before = fake_api.mqtt_info_calls
        await coordinator.async_refresh_settings_now()
        await hass.async_block_till_done()

        assert fake_api.mqtt_info_calls == before
