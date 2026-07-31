# Cramer AiConic for Home Assistant

[![hacs][hacs-badge]][hacs]
[![release][release-badge]][release]
[![license][license-badge]](LICENSE)

Home Assistant integration for **Cramer AiConic** RTK robotic lawn mowers
(Globe `RLM` series), using the same cloud the official AiConic app uses.

Developed and tested against an **RLM3 on firmware 8.0.55**.

> Not affiliated with, endorsed by, or supported by Cramer or Globe Group.

---

## What you get

A single device with everything the app exposes, plus a few things it does not.

### Control

| Entity | Notes |
|---|---|
| `lawn_mower` | Start mowing, pause, dock — with the standard mower card |
| `button` | Start / Pause / Return to base / Refresh status |
| `number` — cutting height | The mower's default height, 20–80 mm |
| `select` | Front light, rear light, sound, obstacle handling, site |
| `switch` | Automatic firmware updates |

### Schedule

The mower's week timers are fully editable. Four slots, each with:

| Entity | Example |
|---|---|
| `switch` — enabled | `on` |
| `time` — start | `09:00` |
| `number` — duration | `240` min |
| `text` — days | `mon,wed,fri` |

Plus a `sensor` whose attributes hold the whole schedule, and
`cramer_aiconic.set_schedule` / `cramer_aiconic.clear_schedule` services.

### Zones and coverage

`sensor.zones` lists the maps defined for your site, each with its health
flags (confirmed, charging station reachable, working area OK, verification in
progress) and a marker for the one currently selected. The mower reports
coverage for whichever map it is working, so `area cut` and `area remaining`
are attributed to the active zone.

### Manual drive

`cramer_aiconic.drive` nudges the mower by hand:

```yaml
- action: cramer_aiconic.drive
  target:
    entity_id: lawn_mower.lawnmower
  data:
    speed: 20            # negative reverses
    angular_velocity: -10
    set_waypoint: false  # drop a mapping waypoint here
```

> The mower only accepts this in mapping/manual mode, and it coasts to a stop
> once commands stop arriving — the app streams them from a joystick. A single
> call is a nudge, not a journey. If the mower refuses, the reason surfaces as
> a waypoint-availability code (`not_in_map_mode`,
> `manual_control_not_available`, `cant_set_no_signal`, …).

### State

`state`, `battery`, `next start or stop`, `next start reason`, `area cut`,
`area remaining`, `estimated time remaining`, `site`, `map`, `zones`,
`operation mode`,
`cutting height` (the blade's current position), `default speed`, `schedule`,
`signal quality`, `LTE signal`,
`firmware`, `last status update`, `last contact age`, and a `device_tracker`
with the mower's GPS position.

Binary sensors: `connectivity`, `charging`, `in charging station`, `problem`,
`RTK fix`, `upside down`.

### When the mower stops reporting

The cloud serves the last datapoints it received from your mower forever, and
never says how old they are. A mower that flattens its battery out on the lawn
therefore looks exactly like one that is quietly docked — its battery, state
and position all keep reading as though they were live.

So the integration tracks the mower's own last sign of life, and treats the
readings as stale once the cloud marks it offline **or** nothing has arrived
for ten minutes. A healthy mower reports every ~30 s, docked or not; over five
days of measurement the longest real gap was 2 minutes.

| | Behaviour when stale |
|---|---|
| `lawn_mower` | `unavailable` — it can only claim what the mower is doing *now* |
| every other entity | keeps its last reading, with a `stale: true` attribute |
| `sensor` — last contact age | minutes since the mower last reported |
| `binary_sensor` — connectivity | `off` |

The MQTT settings pass is skipped while the mower is not reporting, rather
than opening a session and waiting 25 s to fail.

`sensor.<mower>_last_contact_age` is the one to alert on. It is derived from
the cloud's own timestamp rather than from `last_changed`, so it survives a
Home Assistant restart — an age built on `last_changed` silently restarts its
count every time Home Assistant does:

```yaml
- alias: "Mower: lost contact"
  triggers:
    - trigger: numeric_state
      entity_id: sensor.lawnmower_last_contact_age
      above: 15
  actions:
    - action: notify.mobile_app_phone
      data:
        message: >
          The mower has not reported for
          {{ states('sensor.lawnmower_last_contact_age') }} minutes.
```

---

## Installation

### HACS (recommended)

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/DarkoMK/cramer-aiconic-ha`, category **Integration**
3. Install **Cramer AiConic**, restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Cramer AiConic**

### Manual

Copy `custom_components/cramer_aiconic` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Sign in with the same email and password you use in the AiConic app, and pick
your region (the EU, North America and Asia-Pacific endpoints are discovered
from the vendor's public region list).

Options (⚙ on the integration):

- **Polling interval** — default 30 s.
- **Read mower settings** — default on. See *How it talks to the mower* below.

---

## How it talks to the mower

Worth understanding, because it explains the timings you will see.

**State comes over HTTP, every 30 s.** The mower pushes a status frame to the
cloud roughly every 30 seconds all by itself, and the cloud caches it. Reading
state therefore costs one HTTP request and never wakes the mower.

**Settings come over MQTT, every 15 min.** The cloud only caches four
datapoints. Light modes, sound, obstacle handling, speed, selected site, radio
status, GPS and the week timers are answered on AWS IoT MQTT and then thrown
away, so the integration opens a short MQTT session, asks for them, and
disconnects.

That session matters: **the AWS IoT policy pins the MQTT client id to your
account's Cognito identity**, so Home Assistant and the phone app cannot both
hold a connection — whoever connects last kicks the other off. The integration
therefore never stays connected; it is in and out in well under a minute. If
you still find it disturbs the app, turn off *Read mower settings* — you keep
state, control and the lawn mower entity, and lose the settings entities.

**Commands are HTTP and immediate.** Start, pause, dock and every setting write
go out as a single request; the integration then re-reads state a few seconds
later so the UI catches up.

### Timing you should expect

- State and battery: fresh within 30 s.
- A setting or schedule edit: applied immediately, but the value read back from
  the mower takes 40–60 s. The UI shows your edit straight away.
- The mower silently ignores read commands that arrive back to back, so the
  integration paces them ~3.5 s apart.

---

## Automation

The entities are plain Home Assistant entities, so anything works. A
weather-safety example — send the mower home when it starts raining:

```yaml
automation:
  - alias: "Mower: rain detected, return to base"
    triggers:
      - trigger: state
        entity_id: binary_sensor.rain_sensor
        from: "off"
        to: "on"
    conditions:
      - condition: state
        entity_id: lawn_mower.lawnmower
        state: mowing
    actions:
      - action: lawn_mower.dock
        target:
          entity_id: lawn_mower.lawnmower
```

Rewrite a schedule slot:

```yaml
- action: cramer_aiconic.set_schedule
  target:
    entity_id: lawn_mower.lawnmower
  data:
    timer_index: 1
    days: [tue, thu]
    start_time: "09:00:00"
    duration_minutes: 240
    enabled: true
```

---

## Supported hardware

| Model | Status |
|---|---|
| RLM3 | Tested (firmware 8.0.55) |
| RLM1 / RLM2 / RLM4 | Should work — the protocol is shared. Reports welcome. |

Some commands are firmware-dependent. On RLM3 8.0.55 the mower does not answer
the public event log, available wheel speed, relative position, remote-antenna
status or security parameters; the integration simply does not create those
entities.

Note that **Cramer Connect** (the older RLM1/RLM2 app) is a *different* cloud
with a different API. If your app is "Cramer Connect" rather than "Cramer
AiConic", see [claesmathias/cramer-hacs](https://github.com/claesmathias/cramer-hacs).

## Troubleshooting

**Everything goes unavailable for a few seconds, repeatedly.** Something else is
logging in with the same account — a second Home Assistant, a script, or a
family member's phone. The vendor invalidates the previous session on each
password login. The integration recovers automatically, but the fix is one
session per account.

**Settings entities stay unknown.** The settings read needs an MQTT session. If
the phone app is open it will win the connection. Close it and wait for the
next pass, or check the log with:

```yaml
logger:
  logs:
    custom_components.cramer_aiconic: debug
```

**Re-authentication keeps being requested.** The password changed, or the
account is locked. Re-enter it in the integration's *Reconfigure* flow.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The protocol tests run against frames captured from a real mower and need no
hardware or network. See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the wire
format.

## Credits

The protocol was reverse engineered from the official Android app.
[claesmathias/cramer-hacs](https://github.com/claesmathias/cramer-hacs) was a
useful reference for the Home Assistant side, though it targets the other
Cramer cloud.

## License

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[release]: https://github.com/DarkoMK/cramer-aiconic-ha/releases
[release-badge]: https://img.shields.io/github/v/release/DarkoMK/cramer-aiconic-ha
[license-badge]: https://img.shields.io/badge/License-MIT-yellow.svg
