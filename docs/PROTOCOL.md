# Cramer AiConic / Globe RLM protocol

Notes from reverse engineering the official Android app, kept here so the
integration is maintainable by someone other than its author. Everything below
was verified against a live RLM3 on firmware 8.0.55.

## Cloud shape

Three services are involved:

| Purpose | Host |
|---|---|
| Region list (no auth) | `api.globegrouponline.com/globe/world/country/region/list` |
| OAuth2 (GUC) | per region, e.g. `user.cramertools.com` |
| Device API (Globe) | per region, e.g. `api.eu.globegrouponline.com/globe` |
| Live device link | AWS IoT MQTT over WebSocket, SigV4-signed |

### Authentication

OAuth2 password grant against `{guc}/connect/token`, form-encoded, with
`Tenant=GLB`, `Target=gConnect`, the app's client id/secret and a long fixed
scope list. Access tokens last 7200 s.

Two behaviours that dominate the design:

1. **The refresh token is single-use and rotates.** Presenting it returns a new
   one and revokes the old immediately (`invalid_grant` on reuse). If the
   rotated value is lost — a crash before persisting, or two refreshes racing —
   authentication is dead until a full password login.
2. **A password login invalidates the previous session.** Two clients on one
   account will keep 401-ing each other.

The integration therefore serialises all token acquisition behind one lock,
persists each rotated token the moment it arrives, refreshes 10 minutes before
expiry, and falls back to a password login whenever a refresh fails.

### Device endpoints

```
GET  /v2/user/{userId}/subscribe/devices
POST /product/{productId}/device/{deviceId}/send_command    {"payload": "<hex>"}
POST /product/{productId}/device/{deviceId}/get_datapoints  {"parameters": [ids]}
GET  /v4/product/{productId}/device/{deviceId}/last-known-info
GET  /v3/service/get/mqtt
```

Application errors arrive as HTTP 200 with a non-zero `code` in the body;
`5031001` is throttling and should be retried.

### What the cloud caches

`get_datapoints` only ever returns four ids:

| Datapoint | Meaning |
|---|---|
| `746` | Status the mower pushes unprompted every ~30 s |
| `81` | Full status, refreshed only when asked |
| `471` | Cutting height |
| `509` | Operation mode, site name, map name |

Everything else is answered on MQTT and discarded, so reading it means being
subscribed while the request is in flight.

### MQTT

`/v3/service/get/mqtt` returns a Cognito identity and an AWS IoT endpoint. Trade
the identity token for temporary credentials via
`cognito-identity.{region}.amazonaws.com` → `GetCredentialsForIdentity`, then
SigV4-presign `wss://{endpoint}/mqtt`.

Topics:

```
$aws/things/{deviceId}/device_response   answers to send_command
$aws/things/{deviceId}/device_upload     unprompted status pushes
```

**The IoT policy pins the MQTT client id to the Cognito identity id exactly.**
No suffix is accepted, so only one client can be connected per account.

## Wire format

Little-endian throughout. The same framing is used on BLE and in the cloud; the
cloud just carries the frame as a lowercase hex string.

```
u8   messageId          1..255, 0 reads as unset
u16  totalLength        = len(body) + 9
u16  parameterId
u16  bodyLength
...  body
u16  crc16              over the whole frame with these two bytes zeroed
```

A response reuses the shape with `parameterId + 1`, and its body starts at
offset 7.

The CRC is **CRC-16/ARC** (polynomial `0xA001` reflected, init `0x0000`) with a
final XOR of `0xFFFF`.

## Parameter ids

| Id | Request | Body |
|---|---|---|
| 80 | Get mower status | — |
| 94 | Get GNSS position | — |
| 96 | Get radio status | — |
| 132 | Get public event log | `u8 index, u8 count` |
| 156 / 154 | Get / set clock | — / `u32 utc, u32 offset` |
| 226 | Pause | `u16 0` |
| 230 | Park | `u16 minutes` (`0xFFFF` = indefinite) |
| 468 / 470 | Set / get cutting height | `u8 mm` (20–102) |
| 506 / 508 | Set / get operation mode | — |
| 590 | Get firmware package | — |
| 598 | Set week timer | see below |
| 602 | Get all week timers | `site(21) + map(21)` |
| 604 | Clear week timer | `u8 index` (`0xFF` = all) |
| 606 / 608 | Set / get front light | `u8 mode` |
| 610 / 612 | Set / get rear light | `u8 mode` |
| 614 / 616 | Set / get sound | `u8 mode` |
| 618 | Start mowing | `u16 override, u16 pin, u8 option, u8 mapIndex` |
| 640 | Get map coverage | — |
| 670 / 672 | Set / get obstacle handling | `u8 mode` |
| 732 | Get site names | `u16 firstIndex` |
| 746 | Status push (mower → cloud) | — |
| 752 / 754 | Set / get default speed | `u8` |
| 756 / 758 | Set / get selected site | `site(21)` |
| 790 / 792 | Set / get auto-update | `u8 flag, u8 2, u8 4` |

Strings are NUL-terminated, NUL-padded, fixed width, ISO-8859-15.

## Key payloads

### Status push (746, 16 bytes)

```
u8  mainState        u32 nextStartStop   u8  battery
u8  nextStartSource  u16 notification    u8  configHash
u16 eventId          u32 eventTimestamp
```

### Full status (81, ≥15 bytes)

```
u8  returnCode  u8  mainState  u8  subState   u32 nextStartStop
u8  battery     u16 statusFlags u8 wireless   u8  signalQuality
u8  nextStartSource  u16 notification  u8 configHash
```

`statusFlags`: `1` start pressed, `2` in charging station, `4` upside down,
`8` demo mode, `16` enabled, `32` receiving correction data, `64` RTK fix.

### Week timer record (603, 8 bytes each after a return code)

```
u8 index  u8 mapIndex  u8 modeMask  u8 hour  u8 minute  u8 dayMask  u16 minutes
```

`dayMask`: Monday `1` … Sunday `64`. `modeMask`: `1` enabled, `2` defines
operation time, `4` entire time, `8` restart cutting.

Writing a timer (598) is `u8 index + site(21) + map(21) + u8 0 + u8 modeMask +
u8 hour + u8 minute + u8 dayMask + u16 minutes`. **There is no partial update** —
every edit rewrites the whole slot, which is why the integration keeps a
pending-edit overlay while the ~40 s read-back is outstanding.

### Map coverage (641, 7 bytes)

```
u8 returnCode  u16 areaCut  u16 areaRemaining  u16 estimatedMinutes
```

`0xFFFF` for the estimate means the mower has none yet.

## Firmware differences

RLM3 8.0.55 does not answer 132 (event log — returns `INVALID_DATA` for any
arguments), 736 (available wheel speed), 706 (relative position), 632/544/552
(remote antenna) or 12 (security parameters). Other models or firmware may.

## Re-deriving this after an app update

```bash
adb pull "$(adb shell pm path cramer.aiconic.app | head -1 | cut -d: -f2)" base.apk
JAVA_HOME="$(/usr/libexec/java_home)" jadx -d out --no-res base.apk
```

`jadx` fails with a misleading `JAVA_HOME` error if it is not set explicitly.
Parameter ids live in `cramer/aiconic/blelibrary/payloads/requests/**` as
`PARAMETER_ID`, field layouts in each class's `write()` and `fromByteStream()`,
and the OAuth client credentials in `res/values/strings.xml`.

App version 1.3.2 changed nothing relevant versus 1.2.9 — only four new
map-editing parameter ids.
