# Sphere C Bluetooth protocol

This is the canonical wire and state reference for the USB-C
Olight Sphere C investigated in this repository. It reconciles
owned-device traffic, the Olight product panel and APK, current
controller code, and an analyzed Sphere C update image available
to this investigation. The original magnetic-charge Sphere is a
separate, untested target.

For operation and commissioning, see [GUIDE.md](GUIDE.md).
[TECH.md](TECH.md) explains the local implementation;
[FIRMWARE.md](FIRMWARE.md) summarizes firmware findings and
the limits of custom-content support. [REPORT.md](REPORT.md)
explains the findings and [STATUS.md](STATUS.md) tracks remaining
validation. Start at the [project README](../README.md).

## Evidence and notation

| Label | Establishes | Boundary |
|---|---|---|
| Live | Authenticated traffic from owned Sphere C devices | Physical output only when separately observed |
| Visual | Recorded human observation of output | No claim about unobserved firmware versions or internal state |
| Panel | Saved Sphere C schema or product UI call site | Declared capability is not proof of a working device handler |
| APK | Executable behavior in Olight 3.2.5 / Thingclips SDK | Generic SDK branches need product-specific corroboration |
| Firmware | Static code/data in the analyzed update image | Not live execution or byte-for-byte installed-image identity |
| Code | Current repository source and offline verification | No additional hardware validation |
| Standard | Bluetooth SIG definitions | No implied support for optional models |
| Inference | Explanation consistent with observations | Not an independently confirmed fact |

Hex bytes are in wire order. Numeric `0x` identifiers are values,
not instructions to serialize every field alike. `u8`, `u16` and
`s16` describe widths and signedness. SIG multi-byte parameters
are little-endian; Tuya VALUE fields are big-endian. Opcode bytes
have their own format: HSL Set is `82 76`; vendor WRITE is `c9 d0
07`.

An **access message** is an opcode and its parameters. Examples
omit encryption, source/destination, network sequence, transport
MIC and Proxy/GATT framing. They are not raw GATT writes.

The analyzed image is 184,736 bytes, SHA-256
`fe33137bf966d52b80d7813940a583efb9eef52fa7618d7e1d6a295b58304f52`.
Its static revision data is consistent with 0.5. Direct revision
reads independently distinguish installed 0.2 and 0.5 below; they
do not prove byte-for-byte equality with this image. Important
dispatch findings were checked against decoded instructions and
raw tables because the TC32 analysis tools have incomplete
semantics and indirect-branch boundary errors. Absence from
automatic function discovery is not proof of absence. See
[FIRMWARE.md](FIRMWARE.md) for bounded searches.

## Identity and tested scope

| Item | Recorded value | Meaning / source |
|---|---|---|
| Product | Olight Sphere C, USB-C | Tested device generation |
| Composition company ID | `0x07d0` | Tuya namespace used by this Olight product; not a chip ID |
| Composition product ID | `0x0200` | Mesh composition identifier |
| Composition version ID | `0x3530` | Raw composition version; not interpreted as a release string |
| Element count | 6 | One primary and five secondary elements |
| Feature bits | `0x0007` | Relay, Proxy and Friend supported; not a report of enabled state |
| Tuya product ID | `d0dpga0h` | Cloud/panel identifier; distinct from Mesh product ID |
| APK | Olight 3.2.5, version code 239 | Static-analysis artifact |
| Product panel | `0.5.9_RsPYtSG2yNGWYhKJ` | Saved mini-app version |
| Local dependency | `btmesh` revision `a8f7bbbebaeba124d7d08cb16122055074f2ef46` | Pinned in `pyproject.toml` and installed during validation |

Composition was obtained with DeviceKey-authenticated reads.
Addresses are allocations, not model constants. Use primary
address `A` and element offsets below; derive `A` from the owned
network's state. Allocations can change after reprovisioning. The
binding map is below; commissioning is documented in
[GUIDE.md](GUIDE.md).

## Discovery and GATT

The initial observed app session used standard Mesh Proxy GATT
without an observed SMP pairing or link-encryption exchange. Mesh
carries its own authenticated encryption inside GATT; lack of BLE
bonding does not expose the lighting commands. The capture did
not contain the old network's keys. [Capture analysis][capture]

| Advertisement | Relevant data | Interpretation and limit |
|---|---|---|
| Mesh Provisioning `0x1827` | Device UUID and OOB information | Unprovisioned discovery; UUID is public, not a key |
| Mesh Proxy `0x1828`, type `00` | Eight-byte Network ID | Derived from NetKey by `k3`; identifies a subnet, not uniquely a lamp |
| Mesh Proxy `0x1828`, type `01` | Node Identity hash and random | Can be checked using NetKey and a known node address |
| Olight-associated `0xfcf1` | Opaque service data | Stable payload observed across address rotation; exact device mapping unresolved in the old capture |
| Manufacturer `0x07d0` | Tuya-format data | Vendor ecosystem evidence, not silicon identification |

The current scanner uses fresh advertisement callbacks. It
accepts a matching Network ID or a verified Node Identity for the
saved node, then authenticates Mesh responses. Network ID alone
is not a per-device identity check. [Transport
source][transport-code]

### Services and characteristic directions

“In” means client to lamp; “Out” means lamp to client. Short
UUIDs use the Bluetooth base UUID. Handles are observed examples
from one capture, not values to hard-code. [Capture][capture],
[SIG Assigned Numbers][assigned], [Tuya porting guide][tuya-port].

| Service | Data In: write without response | Data Out: notify | Observed use |
|---|---|---|---|
| Provisioning `1827` | `2adb` | `2adc` | Local PB-GATT commissioning |
| Proxy `1828` | `2add` | `2ade` | Authenticated control path |
| Alternate `7fdd` | `2add` | `2ade` | Present in table; not the selected control service |
| Tuya `fd50` | `00000001-0000-1001-8001-00805f9b07d0` | `00000002-0000-1001-8001-00805f9b07d0` | OTA attempt observed; separate SDK packet/transfer parser found in firmware |

| Captured service | Out handle | CCCD handle | In handle |
|---|---|---|---|
| `1827` | `0013` | `0015` | `0017` |
| `1828` | `001c` | `001e` | `0020` |
| `7fdd` | `0025` | `0027` | `0029` |
| `fd50` | `0037` | `0039` | `003b` |

Enable notifications through the selected Out characteristic's
CCCD, then use its paired In characteristic. The duplicated Proxy
UUIDs make lookup by characteristic UUID alone ambiguous. Local
transport selects within the intended service and warns if that
service still contains multiple matches. Its UUID-only fallback
when service information is unavailable is an implementation
limitation, not a recommended discovery guarantee.

### Other observed GATT fingerprints

| Role in Telink SDK | Service UUID | Characteristic UUID |
|---|---|---|
| OTA | `00010203-0405-0607-0809-0a0b0c0d1912` | `00010203-0405-0607-0809-0a0b0c0d2b12` |
| User-defined | `00010203-0405-0607-0809-0a0b0c0d7fde` | `00010203-0405-0607-0809-0a0b0c0d7fdf` |

These pairs and `7fdd` match the official Telink SDK examined in
the [GATT fingerprint audit][chip]. Together they strongly
support Telink software lineage. They do not identify TLSR8258 or
any other exact MCU, establish a usable firmware-read command, or
prove that the original Sphere shares the same implementation.

The analyzed firmware has a packed GATT table with distinct
provisioning, Proxy and FD50 handlers. CCCD callbacks store
subscription state; their existence does not establish frame
upload. Registration, callback routing and the Telink fingerprint
comparison are documented in [FIRMWARE.md](FIRMWARE.md).

### Device Information firmware revision

Service `180a` exposes readable characteristic `2a26`
(`00002a26-0000-1000-8000-00805f9b34fb`). Direct live reads
succeeded without Mesh provisioning or update writes by the
reader. Access may differ on another image; preserve all bytes.

| Installed revision | Ten-byte characteristic value |
|---|---|
| 0.2, independently matched by the app | `00 00 30 32 30 32 14 10 00 02` |
| 0.5, independently matched by the app | `00 00 30 35 30 35 14 10 00 02` |

The duplicate words in bytes 2..3 and 4..5 are little-endian:
`0x3230` is ASCII `02`, and `0x3530` is ASCII `05`. The analyzed
build's initialization supports the dotted interpretation above.
Rendering other values as dotted versions remains provisional.
Composition VID `0x3530` and Tuya product ID `d0dpga0h` have
different roles even where revision bytes coincide.

[scripts/read_firmware_revision.py](../scripts/read_firmware_revision.py)
performs discovery, connection and the characteristic read. It
selects a unique observed candidate or explicit address and
retains raw bytes. It sends no provisioning or update commands.
Accumulated BlueZ service metadata does not establish current
pairing state, ownership or reachability.

## Layers, keys and state

```text
GUI / command-line client
          | private local JSON socket (not a Bluetooth protocol)
        daemon
          | SIG access message or Tuya vendor message
  upper transport: AppKey or DeviceKey protection
  lower transport: segmentation and reassembly
  network: NetKey protection, addresses, SEQ, IV Index
  Proxy PDU: type and GATT segmentation/reassembly
  GATT Data In / Data Out
          |
       Sphere C
```

The Proxy header is one byte: upper two bits SAR, lower six bits
message type. Its SAR values are `00` complete, `01` first, `10`
continuation, and `11` last. This is distinct from lower
transport segmentation inside Mesh. [Mesh
Protocol][mesh-protocol],
[SIG Proxy SAR tests][mesh-tests]

| Proxy message type | Payload |
|---|---|
| `00` | Network PDU |
| `01` | Mesh beacon |
| `02` | Proxy configuration |
| `03` | Provisioning PDU |

| State/key | Role | Local handling and limit |
|---|---|---|
| NetKey, 128 bits | Network encryption/authentication and related identity derivations | Locally generated and private |
| AppKey, 128 bits | Application access protection | Added after provisioning and bound to model instances |
| DeviceKey, 128 bits | Node-specific configuration protection | Derived during provisioning; not sent as plaintext key material |
| SEQ, 24 bits | Network freshness and nonce uniqueness | Advanced cursor persisted before outbound PDU queueing |
| IV Index, 32 bits | Network security context | Default Proxy backend: fixed session value, no complete IV Update/recovery |
| TID, 8 bits | SIG Set transaction identity | Persisted allocator, modulo 256; not a substitute for SEQ |
| Receive replay state | Reject stale authenticated traffic | Default Proxy dependency has immediate-duplicate suppression, not durable standards-complete RPL |

Sequence allocation covers application PDUs, lower-transport
segment acknowledgments and Proxy configuration. State updates
use an exclusive lock, atomic replacement and fsync of file and
directory. Sequence gaps are safe; reusing a nonce is not. Never
copy a network state into two concurrent transmitters or roll
back its sequence cursor. [State][state-code],
[transport][transport-code]

The installed pinned `btmesh` acknowledges and reassembles
incoming segmented unicast traffic. Old comments claiming that
segments are never acknowledged are superseded. Reliable resend
of missing outbound segments is still absent. A whole-message
request retry is not equivalent to transport segment recovery.

The default Python Proxy backend ignores non-network messages
after connection, including beacons. Network decoding checks IVI
and authenticates under the fixed IV Index; it does not implement
a beacon-driven IV Update state machine. Do not describe this as
complete authenticated-beacon mismatch detection or recovery.
These limits describe the default Python Proxy backend. The
experimental BlueZ backend delegates transport, host sequence
state and replay handling to BlueZ; see [TECH.md](TECH.md).

## Provisioning and configuration

Local commissioning uses PB-GATT and standard P-256 ECDH. The
saved app path uses in-band public keys and No OOB; the local
provisioner supports No OOB and optional static OOB. Output/input
OOB and PB-ADV are not implemented. No OOB does not protect
commissioning from an active man-in-the-middle. Local ownership
uses standard provisioning and locally managed keys.
[Provisioning analysis][provision-analysis], [runbook][provision]

| Stage | Exchange / action | Result and evidence |
|---|---|---|
| Discover | Unprovisioned UUID and capabilities | Identify the intended device and number of elements |
| Negotiate | Invite, Capabilities, Start | Select algorithm/public-key/authentication methods |
| Establish shared secret | Public Key exchange, ECDH | Derive common session material |
| Confirm | Confirmation and Random exchange | Validate the selected authentication transcript |
| Provision | Encrypted Data; Complete response | Transfer NetKey, key index, flags, IV Index and primary unicast |
| Reconnect | Mesh Proxy | Node advertises its new network/identity |
| Inspect | Config Composition Data Get | Authenticate layout and allocate all six addresses |
| Authorize | Config AppKey Add; Model App Bind | Install AppKey and bind the 11 matching instances |
| Finish configuration | Network Transmit Set `80 24 15` | Count field 5, interval steps 2; matching Status recorded |
| Verify | AppKey-authenticated OnOff Get | Confirm usable application access and later reconnect |

The DeviceKey is derived with the provisioning salt and ECDH
secret (`k1(..., "prdk")`). Merely recording a standard
public-key provisioning exchange does not reveal the shared keys.

Early local attempts completed provisioning but later rolled
back. The durable run added the remaining allowlisted bindings
and Network Transmit Set. Both are standard configuration
operations; the experiment did not isolate a single firmware
commit trigger. Retention was observed across disconnect and
button-off/wake, then local ownership was recovered after a round
trip through the phone app. None of these proves survival of
battery power loss.

Factory lighting restoration (DP 106) is distinct from Mesh Node
Reset. Reset/reprovisioning changes ownership; the runbook
records the tested recovery procedure rather than making it part
of a normal read or reconnect operation.

## Composition and bindings

All reported element locations were zero. “Bound” below means
included in the successful local AppKey-binding sequence. A
present client or setup model is not another runtime server.
[Composition][models], [secondary-element audit][elements]

| Offset | Model ID | Model | Bound / observed use |
|---|---|---|---|
| `A+0` | `0000` | Configuration Server | DeviceKey configuration |
| `A+0` | `0002` | Health Server | Present, not in bind allowlist |
| `A+0` | `0003` | Health Client | Present, not in bind allowlist |
| `A+0` | `1000` | Generic OnOff Server | Bound; live Get/Set |
| `A+0` | `1001` | Generic OnOff Client | Present, not in bind allowlist |
| `A+0` | `1300` | Light Lightness Server | Bound; live Get/Set |
| `A+0` | `1301` | Light Lightness Setup Server | Present, not in bind allowlist |
| `A+0` | `1302` | Light Lightness Client | Present, not in bind allowlist |
| `A+0` | `1303` | Light CTL Server | Bound; combined CTL not separately live-validated here |
| `A+0` | `1304` | Light CTL Setup Server | Present, not in bind allowlist |
| `A+0` | `1305` | Light CTL Client | Present, not in bind allowlist |
| `A+0` | `1306` | Light CTL Temperature Server | Bound; live Get/Set |
| `A+0` | `1307` | Light HSL Server | Bound; live Get/Set |
| `A+0` | `1308` | Light HSL Setup Server | Present, not in bind allowlist |
| `A+0` | `1309` | Light HSL Client | Present, not in bind allowlist |
| `A+0` | `130a` | Light HSL Hue Server | Present; separate binding/use not established |
| `A+0` | `130b` | Light HSL Saturation Server | Present; separate binding/use not established |
| `A+0` | `07d0:0004` | Tuya vendor server | Bound; DP traffic |
| `A+0` | `07d0:0005` | Tuya vendor client | Not bound or used as a DP server |
| `A+1` | `1000` | Generic OnOff Server | Bound; physical semantics unknown |
| `A+2` | `1000` | Generic OnOff Server | Bound; physical semantics unknown |
| `A+3` | `1000` | Generic OnOff Server | Bound; physical semantics unknown |
| `A+4` | `1000` | Generic OnOff Server | Bound; physical semantics unknown |
| `A+5` | `1000` | Generic OnOff Server | Bound; physical semantics unknown |

There is no Generic Level Server `1002` or xyL family
`130c`–`130e` in this composition. `130a` and `130b` are HSL
Hue/Saturation, not xyL. The five secondary Boolean models have
no demonstrated physical LED, region or color-channel mapping.
Their binding is not proof of independently controlled emitters.

## Standard lighting access

These are opcode values, not little-endian parameter integers.
All rows target the appropriate server at `A+0` on this device.
Unacknowledged variants are documented for interpretation; the
local high-level controller uses acknowledged operations.
[Current access codecs][access-code], [lighting controller][lighting-code],
[SIG model definitions][mesh-model], [assigned opcodes][assigned]

| Model state | Get | Set | Set unacknowledged | Status | Device evidence |
|---|---|---|---|---|---|
| OnOff | `8201` | `8202` | `8203` | `8204` | Live Get/Set |
| Lightness Actual | `824b` | `824c` | `824d` | `824e` | Live Get/Set |
| Combined CTL | `825d` | `825e` | `825f` | `8260` | Present/bound; APK path, not local CLI operation |
| CTL Temperature | `8261` | `8264` | `8265` | `8266` | Live Get/Set |
| HSL | `826d` | `8276` | `8277` | `8278` | Live Get/Set |
| xyL | `8283` | `8284` | `8285` | `8286` | Generic codec only; absent on this Sphere C |

Get has no parameters. Set layouts below are the short forms;
multi-byte parameters are little-endian.

| State | Set parameters | Status parameters, short / extended |
|---|---|---|
| OnOff | `onoff:u8, tid:u8` | `present:u8` / add `target:u8, remaining:u8` |
| Lightness | `lightness:u16, tid:u8` | `present:u16` / add `target:u16, remaining:u8` |
| Combined CTL | `lightness:u16, temperature:u16, delta_uv:s16, tid:u8` | `present_lightness:u16, present_temperature:u16` / add target lightness, target temperature and remaining byte |
| CTL Temperature | `temperature:u16, delta_uv:s16, tid:u8` | `present_temperature:u16, present_delta_uv:s16` / add target temperature, target delta-UV and remaining byte |
| HSL | `lightness:u16, hue:u16, saturation:u16, tid:u8` | Same three color fields / add `remaining:u8`; no target tuple |
| xyL | `lightness:u16, x:u16, y:u16, tid:u8` | Same three fields / add `remaining:u8` |

Status parameter lengths are strict: OnOff 1/3 bytes, Lightness
2/5, combined CTL 4/9, CTL Temperature 4/9, and HSL/xyL 6/7.
Combined CTL Status does not report delta-UV; use Temperature Get
for that field. Ordinary HSL Status has no target tuple; its
target state has separate standard messages. Temperature- only
Set preserves brightness, whereas combined CTL also sets it.

| Field | Units/range | Interpretation |
|---|---|---|
| OnOff | `0` or `1` | Boolean output state |
| Lightness | `0..65535` | Mesh Lightness Actual; zero has off semantics |
| HSL hue | `0..65535` | Normalized hue, conventionally scaled to 360 degrees |
| HSL saturation | `0..65535` | Normalized HSL saturation |
| CTL temperature | `800..20000 K` protocol range | Not a measured emitter range or promised visible color |
| Delta-UV | `-32768..32767` | Signed fixed-point; approximately raw / 32768 |
| xyL x/y | `0..65535` | Normalized chromaticity; unsupported on this composition |

Applicable Sets may append `transition_time:u8, delay:u8` as a
pair. Transition/remaining time packs a two-bit resolution and
six-bit step count; resolutions are 100 ms, 1 s, 10 s and 10 min.
Step count `0x3f` denotes unknown in status and is not a valid
requested transition. Delay uses 5 ms units. Local HSL encoding
exposes only the short Set form and accepts 6/7-byte Status
parameters. [Model specification][mesh-model],
[code][access-code]

Use a new TID for a new logical Set; retransmitting the same
transaction retains its TID. Duplicate detection uses source,
destination and TID within six seconds. The current allocator
persists TIDs but does not throttle rapid modulo-256 reuse; this
matters before designing a high-rate streaming controller.

Range queries documented by the standard/app audit include CTL
Temperature Range Get/Status `8262/8263` and HSL Range Get/Status
`827d/827e`. Their Sphere-specific bounds have not been
established here; they are not exposed by the current lighting
CLI.

### State is not always the value just sent

Historical Lightness, CTL Temperature and HSL Sets returned
authenticated Status with the requested values. A later HSL Get
could return a different, stable tuple: restoring
`(3637,1820,65535)` initially acknowledged exactly, then read
`(3276,1638,65535)`. Calibration showed this was not one fixed
scale factor. Firmware color conversion/quantization is a
plausible explanation, not a decoded implementation fact.
Independent Lightness remained `1956`. [Live lighting
trials][lighting-live]

CTL Temperature has wire-level support, but later desktop trials
produced no obvious visible temperature change. The GUI omits it.
A recorded `18900 K` response is model state, not a measurement
of emitted light. [Operational observations][operations]

## Phone color and brightness mapping

The product panel's color wheel is HSV. Its DP 5 string is not
the payload of an HSL Set: the APK converts it first. Ordinary
power, white brightness and color are routed through SIG models,
even though the panel names them as datapoints. [Color
audit][color],
[phone mode semantics][phone-mode]

| Representation | Order/range | Conversion / behavior |
|---|---|---|
| Panel HSV | Hue `0..360`, saturation/value `0..1000` | Wheel preserves value; color brightness preserves hue/saturation |
| DP 5 string | Four hex digits each: hue, saturation, value | Twelve hex characters, e.g. `000003e803e8` for full red |
| Mesh HSL | Lightness, hue, saturation, each `u16` | HSV converted to HSL, then scaled |
| DP 3 white brightness | `10..1000` | APK scales to Mesh Lightness; nonzero active values are clamped to at least 1 |
| Desktop HSV | Three `0..65535` components internally | `hsv_to_mesh_hsl` rounds converted fields; QML slider position is logarithmic |

For normalized HSV saturation `s` and value `v`, the local GUI
implements:

```text
l = v * (1 - s / 2)
s_hsl = 0                         when l is 0 or 1
s_hsl = (v - l) / min(l, 1 - l)   otherwise
mesh_l = round(65535 * l)
mesh_s = round(65535 * s_hsl)
```

Hue is unchanged in normalized space. Reverse conversion uses `v
= l + s_hsl * min(l,1-l)` and, for nonzero `v`, `s = 2 *
(1-l/v)`. This documents the current GUI arithmetic; it does not
claim identical rounding in the APK or firmware. No
gamma-correction step was found in the examined panel/APK path.
[GUI source][gui-code]

The phone panel requests color mode with DP 2, then DP 102. Its
Color Wheel choice is DP 102=`7`, followed by DP 5 updates.
Moving the brightness slider during a color preset writes color
data without reselecting DP 102. The local GUI likewise changes
HSV value via HSL when it knows color/effect mode is active. The
effect of that update on each firmware animation remains
incompletely observed. Firmware tracing qualifies the mode
request: ordinary DP 2 records have no local action in this
image. DP 102 itself sets power on and color mode. See the
separate one-byte mode path
[below](#work-mode-dp-2).

## Tuya vendor messages

DP messages target vendor server `07d0:0004` on the primary
element. The company field is little-endian inside the three-byte
opcode. The embedded `07d0:0005` client is not an alternate DP
server. [DP inventory][dp-inventory], [DATA codec
audit][dp-codec],
[current vendor implementation][vendor-code]

| Operation | Access opcode bytes | Parameter layout |
|---|---|---|
| Acknowledged WRITE | `c9 d0 07` | Ordinary form: `command_type:u8` followed by records; one-byte mode form below |
| Unacknowledged WRITE | `ca d0 07` | Same record layout; no DATA required |
| READ | `cc d0 07` | `command_type:u8, count:u8, ids:u8[count]` |
| DATA | `cd d0 07` | `command_type:u8` followed by zero or more records |

DATA and WRITE have **no record count**. This ordinary format has
no vendor transaction ID. Records continue to the end of the
access message. The READ encoder supports 1–255 IDs; the daemon
additionally rejects duplicates. DP ID 0 is an APK all-DP query
convention, not a product-schema DP or a dedicated public API. A
historical ID 0 query and a read requesting all 17 schema IDs
returned the same five typed records, exposing no extra state.

**Firmware processing is narrower than the generic grammar.** The
product callback passes a family-1 payload to a dispatcher that
selects its first DP ID and returns; no iteration over following
records was found. A multi-record message may be encodable and
echoed while later records have no action. Use separate messages
when independent actions are required; the combined vectors below
are codec examples only.

For `c9`, the callback echoes the payload as `cd` **before**
dispatching it. A matching echo therefore proves receipt, not
application, value validation or feature support. This explains
why an unhandled DP can appear to acknowledge successfully.

### Record fields

Each record starts with `dp_id:u8, dp_type:u8`. The value is:

| Type | Byte | Encoding after type | Local decoding |
|---|---|---|---|
| Raw | `00` | `length:u8, bytes[length]` | Opaque bytes |
| Boolean | `01` | One byte | Zero false, nonzero true; encoder emits 0/1 |
| Value | `02` | Four-byte big-endian integer | Unsigned `0..4294967295` in local codec |
| String | `03` | `length:u8, UTF-8[length]` | Length counts bytes, not characters |
| Enum | `04` | One byte | Index into the product's enum list |
| Bitmap | `05` | `length:u8, bytes[length]` | Big-endian, length restricted to 1, 2 or 4 |

The codec rejects unknown types, truncated headers/values,
missing length, invalid bitmap widths and invalid UTF-8. A
generic encodable record is not automatically a supported product
write. The high-level API restricts mutations to visible preset
selection/activation and DP 101 countdown.

### Command types and response selection

| Command type | Evidence | Current controller policy |
|---|---|---|
| `01` | Ordinary APK and live DP DATA | Read and write response accepted |
| `05` | Alternate ordinary APK DATA/read form | Read response accepted; write response excluded |
| `00` | APK dispatches as generic status | Not parsed as typed DP records |
| `86` | APK group-association command family | Separate grammar; not a DP response |

The meaning of the APK flag selecting READ type `05` is unknown.
Do not assign it a product meaning or assume type echoing without
evidence. The local reader emits type `01`. Live alternate-type `05` READ
probes still returned ordinary type `01` records; command-type echo
is not required. The flag's broader SDK meaning remains unknown.

The node's initial waiter matches response source and opcode, not
vendor command type or requested IDs. The vendor controller
therefore filters newly received DATA and collects within a
bounded window. Reads accept ordinary types `01`/`05`; writes
accept only `01`. An unrelated type `00` must not poison a later
ordinary response. Its first byte is a command type, not DP RAW
type `00`. No authenticated captured type-`00` message
establishes its Sphere-specific semantics. A one-byte mode 0
snapshot prefix is recognized only in the complete product
signature below.

Read completion waits for requested typed IDs (excluding DP 2),
or the fixed five-record snapshot. When DP 2 was requested and a
bare candidate mode prefix is present, completion also requires
validated mode or a typed DP 2. The collection window has its own
five-second budget after the first reply; a fixed 100 ms settle
alone is insufficient. Missing/empty results stay partial at the
deadline. The current 100 ms settle and 50 ms polling intervals
are client policy, not device timing guarantees.

Response correlation is source/opcode/window based inside the
current authenticated Mesh context. Neither network sequence nor
SeqAuth is echoed as a vendor request ID. Unsolicited DATA can
have the same shape as a reply. Requests are serialized per node;
a matching publication is fresh state, not uniquely tied to one
READ. Group queries would need separate collection by unicast
source. The APK's looser waiter compares eight-address node blocks
and expected opcode only; current server-element matching should
not be weakened to that rule.

The collector keeps old received objects alive during a request,
so eviction from the bounded receive history cannot recycle their
Python identities and hide new replies. Earlier missing-DP
reports caused by that host bug do not establish radio loss,
preset-specific failure or firmware omission.

Writes accept an empty type-`01` acknowledgment or a typed DATA
containing an expected DP. A late echo for an unrelated DP is
ignored while waiting for a usable response. The service checks
reported relevant values against requested values. No request ID
exists to eliminate all ambiguity from delayed empty replies.

| Result | Meaning | What to do with it |
|---|---|---|
| Empty DATA | Expected response received, no value | Record acknowledgment only |
| Typed matching echo | Response contains a value, possibly just pre-dispatch input echo | Require independent readback/output evidence for application |
| Independent READ/Get | A subsequent request reports state | Preserve missing IDs and actual returned values |
| Contradictory typed value | Firmware reports something else | Return protocol error; do not claim success |
| Timeout | No expected reply in the request window | Outcome may be uncertain; inspect state before another mutation |
| Visual observation | Human saw the output | Record separately; API never claims this automatically |

Vendor requests use a five-second timeout per node attempt. Reads
inherit the dependency's one whole-message retry; writes set zero
retries there. After the first read response, burst collection
receives a separate bounded timeout. This does not supply unique
transaction correlation or guarantee that every requested DP will
arrive.

The daemon has one exception: after DP 2 acknowledges during
`effect.activate`, it waits 250 ms and sends DP 102. If that
selection gets no DATA and raises `VendorUnavailable`, it repeats
only DP 102 once. It never retries the full activation or the
mode write. `effect.select` and countdown writes have no such
retry. A contradictory echo or parsing failure is not retried.
[Daemon][daemon-code], [timeout audit][timeout]

## Product datapoints

The saved Sphere C schema has 17 entries. Access is the schema's
declaration; it does not prove a live read/write or vendor route.
`R/W` means both, `R` read-only and `W` write-only. Generic DP 4
is deliberately excluded from this product table.
[Inventory][dp-inventory]

| DP | Name | Schema access/type | Range / format | Route and evidence |
|---|---|---|---|---|
| 1 | `switch_led` | R/W boolean | 0/1 | APK routes ordinary control to SIG OnOff; live SIG access |
| 2 | `work_mode` | R/W enum | Schema 0..3; setter accepts 0..4 | Ordinary DP 2 has no local action; separate one-byte mode path below; historical echo alone does not prove mode application |
| 3 | `bright_value` | R/W value | `10..1000`, step 1 | APK maps white brightness to SIG Lightness |
| 5 | `colour_data` | R/W string | HSV, 12 hex digits; schema max 255 | APK converts to SIG HSL; not an observed vendor string |
| 6 | `scene_data` | R/W string | Schema max 255 | Generic APK compressor; no ordinary firmware write action or live scene validation |
| 7 | `countdown` | R/W value | Schema 0..43200; firmware setter 0..86400 | Live zero read; shares firmware countdown with DP 101 |
| 8 | `music_data` | W string | Schema max 255; four-byte raw stream | Firmware unpacks compact state, gated on work mode 3; no local live streaming validation |
| 33 | `power_memory` | R/W raw | Max 255 bytes | No ordinary firmware write action; alternate route/meaning unestablished |
| 37 | `rtc_timer` | R/W raw | Max 128 bytes | No ordinary firmware write action; payload semantics unresolved |
| 38 | `timer_report` | R/W raw | Max 128 bytes | No ordinary firmware write action; payload semantics unresolved |
| 101 | `countdown_2` | R/W value | Schema 0..43200; firmware setter 0..86400 | Live zero write/echo/readback; firmware expiry toggles power |
| 102 | `colour_mode` | R/W enum | `0..31` | Built-in preset selector; firmware also sets power on/color mode and may update a button slot |
| 103 | `battery_percentage` | R value | `0..100%`, step 1 | Live VALUE; firmware report producer, no ordinary inbound action |
| 104 | `sleep_switch` | R/W boolean | 0/1 | Live read false; firmware stores byte persistently and uses it in sleep eligibility |
| 105 | `game_flag` | R/W boolean | 0/1 | No ordinary inbound action; firmware emits false on internal state transition; activation unresolved |
| 106 | `factory_mode_reset` | W boolean | Panel writes true | Firmware resets slots/output/default state; local branch ignores supplied Boolean, so false is not a safe query |
| 107 | `water_mode` | R/W boolean | 0/1 | No ordinary firmware write action; function/activation unresolved |

Here, "no ordinary firmware write action" means no branch in the
ordinary family-1 dispatcher reached through the traced
SDK/application callbacks. It does not exclude another protocol
or firmware version. Ordinary vendor writes for DPs 1/3/5 also
have no local branch; their established controls use SIG models.

DP 4 (`temp_value`) is generic APK functionality mapped to CTL
Temperature. The Sphere C panel has neither this schema entry nor
a temperature slider. Its absence from the panel does not negate
the authenticated CTL model responses.

Live reads establish typed vendor records for DPs 7,101,102,103
and 104. Typical recorded values are zero countdowns, preset 7
(Color Wheel), battery 100 and sleep false; these are
observations, not defaults or fixed values. The product callback
builds this fixed status set rather than strictly filtering
requested IDs. Mode is reported outside the typed records, as
detailed below.

### Selected-preset and mode readback

Use `cc d0 07 01 01 66` for DP 102, or `cc d0 07 01 02 02 66` for
DP 2 and DP 102. No companion ID or special request ordering is
necessary for the fixed snapshot. The normal optional SDK getter
is unregistered in this image; the product callback emits current
mode followed by five typed records rather than projecting the
requested ID list.

Read-only probes on ARI, BEN and CAT tested `[102]`, `[2,102]`,
`[7,101,102,103,104]` and alternate type 05 `[102]`. All twelve
trials produced the same six-message order:

```text
cd d0 07 MM
cd d0 07 01 07 02 C3 C2 C1 C0
cd d0 07 01 65 02 C3 C2 C1 C0
cd d0 07 01 66 04 NN
cd d0 07 01 67 02 B3 B2 B1 B0
cd d0 07 01 68 01 SS
```

`MM` is a bare mode byte, `NN` the selected DP 102 preset,
`C3..C0` the big-endian countdown, `B3..B0` battery percentage,
and `SS` sleep policy. DPs 7 and 101 read the same stored
counter. DP 102 arrived 358–426 ms after each request; the
complete burst ended after 578–676 ms. These are measured
timings, not deadlines that future firmware promises to meet. The
reported selections were ARI2 (Aurora), BEN0 (Flame), CAT28
(Phantom Purple).

A prior read represented its typed data as a combined snapshot;
that representation does not prove a single response message. The
directly observed current burst is six access messages. Proxy SAR
and lower-transport segmentation are separate framing layers and
do not explain away the need to collect the burst.

DP 102 is the **selected preset**, even if another work mode is
rendering. Its static serializer reads the preset independently
of mode. A preset report alone does not establish active color
mode, current motion or visible output.

The controller recognizes a bare byte as mode only when it is
0..4, first in the read burst, and followed by exactly this
ordered typed signature, all with ordinary command type 01:

```text
(7, VALUE), (101, VALUE), (102, ENUM), (103, VALUE), (104, BOOL)
```

An isolated `cd d0 07 01` is ambiguous with an empty ordinary
acknowledgment and never establishes mode 1. Incomplete or
unrecognized bursts may report DP 102 while leaving mode unknown.
No synthetic typed DP 2 record is inserted into the wire records.

Current `effect.get` reports validated mode with
`work_mode_source="readback"`; `effect_active` is true in mode 1.
The snapshot adds `work_mode` and
`work_mode_encoding="bare_snapshot"`, preserving typed records
separately. A decoded bare mode satisfies requested DP 2 for
`missing_dp_ids`. Cached values retain their separate source
labels; an old selection is not promoted to fresh readback.

Live read-only checks on the three devices reported mode 1 with
their respective presets. Modes 0..4 have simulated burst
coverage; only mode 1 was live-validated in these checks. This
complete-burst read supersedes claims that DP 2 is unreadable or
that startup mode must always be inferred from power/preset
caches.

### Work mode: DP 2

| Decimal / hex | Schema name | Evidence boundary |
|---|---|---|
| 0 / `00` | `white` | Firmware selects white output and applies white brightness |
| 1 / `01` | `colour` | Firmware selects current DP 102 preset; DP 102 itself forces this mode |
| 2 / `02` | `scene` | Accepted, but no explicit lighting setup/apply branch in traced functions |
| 3 / `03` | `music` | Firmware selects internal output 36 and permits DP 8 updates |
| 4 / `04` | Undocumented | Setter accepts it; purpose and useful behavior unestablished |

The firmware's mode selector is **one parameter byte** after
vendor opcode `c9 d0 07` or `ca d0 07`, e.g. complete access
message `c9 d0 07 03` for music. The application routes payload
length less than two to the mode setter, then setup/apply. Do not
send an empty payload: this path reads the first byte. These
commands are statically derived and not live-validated.

Ordinary record `01 02 04 03` instead reaches the DP dispatcher,
where DP 2 has no action. Historical DP 2/DP 102 sequences cannot
isolate the mode write because DP 102 selects color itself. The
current activation command still sends the ordinary typed DP 2
first; it does not implement this one-byte candidate path.

The [firmware mode trace][fw-modes] identifies two writers of the
mode byte: the common setter and a guarded standard Lightness Set
branch that writes white 0 directly. The physical-button worker
also invokes reverse effect mapping, selecting white 0 or color 1
and updating DP 102. An HSL update outside color mode selects
color 1 and Color Wheel 7; inside color mode it can retain the
current preset. These implicit transitions matter when recovering
app state.

No identified internal caller selects mode 4. Its only entry is
the generic one-byte command; it is stored/reported but has no
distinct rendering branch. It appears reserved/unimplemented,
rather than a discovered game, water or button-slot mode. Its
intended vendor label remains unknown. The mode byte is separate
from the internal effect code and the hardware slot selector.

### Presets: DP 102

Canonical names and enum order come from the product schema.
Labels/slugs below match current `effect_catalog()`. “Grid” means
one of 25 desktop choices; “Wheel” is the separate color control;
“Hidden” means absent from the saved picker and blocked by the
guarded write API. Hidden effects have not all been
live-validated.

The current guarded UI retains that policy. Firmware setup has a
mapping for every enum 0..31, including hidden ones; mapping
alone does not validate every effect's visible behavior.

Motion `?` means unclassified, not static. The 25 gallery presets
have recording-based classifications: six steady patterns and
19 animations. Hidden entries remain unclassified unless
separately observed. The names come from Panel/Code evidence;
recorded motion is separate Visual evidence. Earlier dedicated
wire experiments covered a subset of the catalog. See the
[recorded asset catalog](../src/spherosphere/gui/assets/presets/catalog.json),
[recording summary][motion], and [selector source][vendor-code].

| Dec | Hex | Canonical | Label | Slug | UI | Motion |
|---|---|---|---|---|---|---|
| 0 | `00` | `flame` | Flame | `flame` | Grid | Recorded animation |
| 1 | `01` | `colour_1` | Cotton Candy | `cotton-candy` | Grid | Recorded animation |
| 2 | `02` | `colour_2` | Aurora | `aurora` | Grid | Recorded animation |
| 3 | `03` | `colour_3` | Fireworks | `fireworks` | Grid | Recorded animation |
| 4 | `04` | `colour_4` | Fountain | `fountain` | Grid | Recorded animation |
| 5 | `05` | `colour_5` | Firefly | — | Hidden | ? |
| 6 | `06` | `colour_6` | Spiral | `spiral` | Grid | Recorded animation |
| 7 | `07` | `colour_7` | Color Wheel | `color-wheel` | Wheel | Solid-color interaction |
| 8 | `08` | `colour_8` | Red Flash | `red-flash` | Grid | Recorded animation |
| 9 | `09` | `colour_9` | Confetti | `confetti` | Grid | Recorded animation |
| 10 | `0a` | `colour_10` | Butterfly | `butterfly` | Grid | Recorded animation |
| 11 | `0b` | `colour_11` | Fireball | `fireball` | Grid | Recorded animation |
| 12 | `0c` | `colour_12` | Gradient | `gradient` | Grid | Recorded animation |
| 13 | `0d` | `colour_13` | Carousel | `carousel` | Grid | Recorded animation |
| 14 | `0e` | `colour_14` | Floating | — | Hidden | ? |
| 15 | `0f` | `colour_15` | Magic Ball | `magic-ball` | Grid | Recorded animation |
| 16 | `10` | `colour_16` | Lilac | `lilac` | Grid | Recorded steady pattern |
| 17 | `11` | `colour_17` | Sunset | `sunset` | Grid | Recorded steady pattern |
| 18 | `12` | `colour_18` | Vibrant | `vibrant` | Grid | Recorded steady pattern |
| 19 | `13` | `colour_19` | Autumn | — | Hidden | ? |
| 20 | `14` | `colour_20` | Red Light | `red-light` | Grid | Recorded steady pattern |
| 21 | `15` | `colour_21` | Blue sky | `blue-sky` | Grid | Recorded steady pattern |
| 22 | `16` | `colour_22` | New Year | — | Hidden | ? |
| 23 | `17` | `colour_23` | Christmas | — | Hidden | ? |
| 24 | `18` | `colour_24` | Harvest | `harvest` | Grid | Recorded steady pattern |
| 25 | `19` | `colour_25` | Willow Wisp | `willow-wisp` | Grid | Recorded animation |
| 26 | `1a` | `colour_26` | Zombie | `zombie` | Grid | Recorded animation |
| 27 | `1b` | `colour_27` | Snowfall | — | Hidden | ? |
| 28 | `1c` | `colour_28` | Phantom Purple | `phantom-purple` | Grid | Recorded animation |
| 29 | `1d` | `colour_29` | Christmas | `christmas` | Grid | Recorded animation |
| 30 | `1e` | `colour_30` | Candy Cane | `candy-cane` | Grid | Recorded animation |
| 31 | `1f` | `colour_31` | Snowfall | `snowfall` | Grid | Recorded animation |

The two Christmas and two Snowfall labels are distinct enum
entries. Use canonical values or slugs, not translated labels as
identifiers. Other panel translations include Fireplace, Small
Rocket, Cheerleaders and Magic Blue Flame; none proves motion.
There are 32 schema entries, 26 picker choices including the
wheel, and 25 desktop grid entries.

`effect select` (also `effect set`) writes DP 102 alone;
`effect activate` retains the typed DP 2 then DP 102 sequence
and the 250 ms settle described above. Both accept only the 26
visible values. Unique slugs and canonical names such as
`colour_2` resolve to the same value; hidden entries have no slug
and are rejected by the guarded write API. Catalog/current-effect
responses retain `slug` and `canonical` metadata.

Live DP 102 writes include a same-value Color Wheel 7 write,
Aurora 2 selection and independent readback, then restoration to
Color Wheel 7 with readback. Sequential activation was visually
confirmed, and the friendly `aurora` spelling was independently
read back as canonical `colour_2`. The sequence does not isolate
the DP 2 step: firmware DP 102 itself selects mode 1 and powers on.
An empty DATA acknowledgment alone proves no visible change.

Firmware setup maps enums 0..21 to internal IDs 2..23. Enums
22..30 map to IDs 25..33; enum 31 maps to ID 30, aliasing enum 27
in this table. Values above 31 are stored but bypass the table;
they are not an established extension mechanism.

DP 102 also sets power on and mode 1 before setup/apply. Setup
can update one of four hardware-button preset slots in modes
4..7, avoids duplicates, and excludes internal IDs 10 and 22.
These are hardware-button slot-selector modes, distinct from the
work-mode byte whose accepted range is 0..4. Slot persistence and
the complete remote selection sequence are not yet established.
Treat selection as potentially having stored-preset side effects,
not just a temporary display change.

### Countdown: DPs 7 and 101

Both firmware setters parse four big-endian bytes and accept
0..86400. They share one stored counter and timer, while also
storing different selector bytes whose further meaning is
unestablished. The product schema and guarded API retain the
narrower `0..43200` seconds range. Zero cancels the timer.

The timer reports remaining time in a DP 7-shaped record and
rearms. At count 1 it reports zero, **toggles power**, applies
output and reports power. This is not intrinsically an always-off
timer. Positive-duration expiry, exact scheduling and the larger
range remain static findings without live validation. A neutral
DP 101 zero write has live echo and independent readback. See
[FIRMWARE.md](FIRMWARE.md) for the behavior summary.

### Music: DP 8

The panel writes work mode `music` and repeatedly supplies
microphone-derived DP 8 values. Generic source text consists of
one mode hex digit, then four hex digits each for hue,
saturation, value, brightness and temperature. The APK compresses
this to a four-byte RAW vendor value, retaining compact
mode/hue/saturation/value; the panel fixes saturation to 1000.

The firmware accepts a four-byte RAW value in work mode 3.
The compact fields describe mode, hue, saturation and value;
the exact packing still needs independent streaming validation.


DP 8 does not itself select music mode. It can set power on
before its mode check, so an ignored update may have side
effects. Ordinary typed DP 2 music writes have no action in the
analyzed dispatcher; the separate one-byte mode path is the
static candidate described above.

Rendering changes whole-engine state. Zero in the first color
field selects zero; a nonzero value invokes a pseudorandom
generator instead of preserving that hue. SDK field names do not
establish exact hue fidelity. This record has no LED indices or
array of colors. No local live streaming validation or public
production music API is established.

Understanding and implementing this mode is the first project
priority. The [technical roadmap](TECH.md#future-work) separates
that planned work from the wire and firmware facts above.

### Sleep, defaults, game and water

DP 104 stores its byte persistently and affects sleep
eligibility. Zero bypasses one idle-counter path when other
wake/sleep flags are clear; other flags can still select sleep.
Nonzero participates in eligibility alongside off state and
activity flags. It is not established as a universal never-sleep
switch. Exact delays and radio-wake behavior remain untested.
Settings become dirty and are later flushed; processing-call
counts are not elapsed seconds.

DP 106 ignores the supplied Boolean in the traced local branch.
It clears output state, sets power off, restores defaults and
marks settings dirty. Four button slots default to internal IDs
3,4,5,6, corresponding to DP 102 enums 1,2,3,4. Broader settings
also reset. **False is still a reset candidate**, not a read. The
traced branch does not establish Mesh ownership reset; Mesh Node
Reset is a separate configuration operation.

DP 105 has an internal false-report producer associated with a
latch and internal effect 24. No ordinary inbound true action or
confirmed game activation route was found. The inspected button
and default paths supply no effect 24 seed. Music's internal ID
`0x24` is decimal 36, a different state. DP 107 has no confirmed
action or report producer in inspected routes. Schema names alone
do not establish working game or water features.

### Clock payload family 2

Besides ordinary family 01, the application accepts family 02.
The handler uses flags at payload offset 2: bit 2 enables a
big-endian 32-bit field at offsets 3..6; bit 1 enables a
big-endian 16-bit field at offsets 7..8. It calls clock/calendar
conversion and a 16-bit state setter.

Offset 1, epoch, signed timezone interpretation and complete
length/flag validation are unresolved. Missing flags supply zero
arguments in the traced handler; they do not establish leave-
unchanged semantics. This is a separate clock path, not DP 37
scheduling, a safe clock query, or scene/pixel upload.

## Operations and examples

| Intent | Current sequence | Important distinction |
|---|---|---|
| Read power | SIG OnOff Get | No output change |
| Select a preset | DP 102 WRITE | Firmware also turns power on and selects color mode; live applicability remains image-specific |
| Activate a preset | Existing client: DP 2=`colour`; wait; DP 102 WRITE | Historical sequence works, but DP 2 echo does not establish its action; DP 102 can explain activation |
| Activate solid color | Activate Color Wheel; HSL Set | Current GUI path; subsequent wheel edits update HSL |
| Phone picker entry | Power on if needed; DP 2; DP 102 | Phone's power step is not automatically part of daemon activation |
| Read countdown | READ DP 101 | Preserve other snapshot records and missing-ID information |
| Set/cancel countdown | DP 101 VALUE; zero cancels | Firmware expiry toggles power; positive expiry not live-validated |

Preset selection and current mode are separate API state.
Connection-generation caches can become unknown on reconnect;
complete `effect.get` snapshots now recover mode directly.
Reported mode 1 identifies the color/preset engine, not visible
motion or an emitter measurement. [TECH.md](TECH.md) describes
cache and connection behavior.

### Synthetic access-message vectors

These vectors were checked against the local codecs. They are
illustrations, not newly captured device traffic or permission to
transmit a write. A real sender supplies the correct keys,
binding, address, fresh sequence and appropriate TID.

| Intent | Complete access-message bytes |
|---|---|
| OnOff Get | `82 01` |
| HSL Set: L=32768, H=0, S=65535, TID=42 | `82 76 00 80 00 00 ff ff 2a` |
| HSL Status for that tuple | `82 78 00 80 00 00 ff ff` |
| READ DPs 2 and 102 | `cc d0 07 01 02 02 66` |
| Empty DATA acknowledgment | `cd d0 07 01` |
| Ordinary DP 2 write, no local action in captured image | `c9 d0 07 01 02 04 01` |
| Its pre-dispatch echo | `cd d0 07 01 02 04 01` |
| Separate one-byte color-mode candidate, static only | `c9 d0 07 01` |
| Separate one-byte music-mode candidate, static only | `c9 d0 07 03` |
| WRITE Aurora selection | `c9 d0 07 01 66 04 02` |
| DATA Aurora echo | `cd d0 07 01 66 04 02` |
| WRITE countdown zero | `c9 d0 07 01 65 02 00 00 00 00` |
| DATA countdown zero | `cd d0 07 01 65 02 00 00 00 00` |

In the READ, the first `02` after command type is the count and
the second is the DP ID. In the mode WRITE, `02` is the DP ID,
`04` the enum type and `01` its value. There is no WRITE count.

A synthetic combined snapshot matching recorded values is one
access message, displayed across lines for readability:

```text
cd d0 07 01
07 02 00 00 00 00
65 02 00 00 00 00
66 04 00
67 02 00 00 00 64
68 01 00
```

The five rows encode countdown, panel countdown, Flame, battery
100 and sleep false. This synthetic 28-byte access message
requires Mesh lower-transport segmentation. It tests combined
record parsing; it is not the six-message live-burst format
documented above. GATT Proxy SAR is another layer.

A combined mode/selection WRITE can be encoded as `c9 d0 07 01 02
04 01 66 04 02`. The panel's group path publishes both DPs
together, but current individual-device activation uses two
sequential writes. Do not substitute this combined vector for
evidence that both changes completed on the physical device. In
the captured firmware's first-record-only DP dispatcher, the
leading DP 2 has no local action and the following DP 102 is not
iterated. This combined vector must not be used as an activation
recipe on the strength of codec validity or an input echo.

### Additional codec vectors and malformed-message rules

The generic DATA grammar permits these exact vectors. They do not
imply that stock Sphere C reads emit typed DP 2:

```text
# Typed colour mode plus Flame
cd d0 07 01 02 04 01 66 04 00

# Typed colour mode plus Aurora, alternate command type
cd d0 07 05 02 04 01 66 04 02

# Typed music mode plus colour_31
cd d0 07 01 02 04 03 66 04 1f

# Alternate READ of DP 2 and DP 102
cc d0 07 05 02 02 66
```

DATA record parsing starts after the command byte and runs to
end. `01 66 04 02` is an Aurora report, with no count byte. A
decoder must reject unknown record types, truncated headers,
missing lengths, truncated values, invalid bitmap widths and
invalid UTF-8. It must not retain a successful-looking partial
record list after malformed trailing data. BOOL decoding treats
nonzero as true; the encoder emits only 0/1. VALUE and BITMAP are
unsigned in the local codec; the transport names alone do not
supply product-specific signedness or units.

## Other vendor model and non-DP surfaces

Tuya assigns packed model `0x07d00004` to its vendor server and
`0x07d00005` to its vendor client. Composition exposes both;
Olight's ordinary DP actions and binding allowlist use only the
server. The external controller supplies client behavior. There
is no known Sphere-specific application GET for the embedded
client and no captured client-originated feature to document. A
Configuration Vendor Model App Get could inspect bindings using
DeviceKey, but would reveal configuration rather than implemented
application actions. Such a model-specific read is not
established by this reference.

Model IDs are not ordinary access-message fields. Opcode,
address, AppKey binding and registered handlers determine
routing. Seeing DATA does not alone identify its originating
model. The APK contains no literal `0x07d00005` action; that
negative search does not exclude computed IDs or native code.
Tuya's public table marks `cb d0 07` reserved; the APK also has
distinct TID-bearing status paths. Neither changes the non-TID
`cc`/`cd` grammar documented here. See the
[Tuya vendor model specification][tuya-vendor].

| Surface | Established behavior | Boundary |
|---|---|---|
| DP 6 scene codec | Generic normal form: one-byte header +13-byte units; compact form: two-byte header +four-byte units | Packed semantics and product use unresolved; ordinary local dispatcher has no DP 6 action |
| Group association | APK WRITE parameters `86 02 <group-address>`, response `86 01 00` | Separate grammar, not DP 86; no family 86 action in the traced product callback |
| Bootstrap opcodes | SDK special-routes C4/C0/C2/C7/E0/C5 with company bytes `d0 07`; related replies C6/C3/C1 | Bootstrap/address state, no identified renderer call |
| Queue acknowledgment | Firmware prefilter handles `cf d0 07` separately | Not an additional DP read/write or pixel upload |
| Secondary OnOff servers | Present and bound at offsets 1..5 | No demonstrated physical mapping or runtime region sweep |
| Host animation | HSL can express successive whole-lamp colors | No guaranteed rate, backpressure, durable replay support or per-pixel interface |
| USB-C | Charging without enumeration in tested states | Factory/debug access unresolved |

DP IDs 0,4 and 86 must not be added to the 17-entry product
schema on these grounds. DP 7 is countdown; product music is DP
8. DP 102 selects built-in firmware presets; generic DP 6 scenes
are a distinct concept.

### Renderer and external reachability

The manual lists 41 colored LEDs and 67 white LEDs. Static code
has a **logical** 41-pixel renderer with indices 0..40 and two
41-entry color buffers. Its virtual 11x5 lookup contains every
logical index once plus 14 padding entries, with nonpadding row
counts 11,11,10,6,3. This is not a measured physical LED layout.
It does not establish mechanical rings, orientation, wiring or a
mapping between logical indices and the six Mesh elements.

No traced ordinary DP, clock payload or identified FD50 transfer
command reaches that internal pixel setter as arbitrary uploaded
frames. An internal indicator uses logical index 5 after sampled
hardware inputs; its external trigger remains unverified. No
remote pixel index, frame buffer, segment map or upload format is
known. The original findings are summarized in
[FIRMWARE.md](FIRMWARE.md).

## Proprietary update transport boundary

Firmware updating is separate from ordinary Mesh lighting
commands. No supported scene-upload interface has been found.
Spherosphere does not implement firmware updates. Update
internals and firmware acquisition procedures are outside this
public reference.

## Evidence coverage and remaining limits

| Topic | Established | Unresolved |
|---|---|---|
| Ownership | Local provisioning, authenticated configuration, retained access after disconnect/button wake | True battery-loss persistence; isolated firmware commit trigger |
| Standard lighting | Primary OnOff, Lightness, HSL and CTL Temperature Get/Set | Physical CTL effect; settled HSL quantization; emitter gamut |
| Vendor reads | DPs 7,101,102,103,104 and validated bare mode 1 in complete bursts | Other live modes; alternate firmware response shapes |
| Effects | Independent selection/readback trials; recorded classification of all 25 gallery presets (six steady, 19 animated) | Hidden-preset live behavior; firmware-version equivalence |
| Countdown | DP 101 zero write and independent readback | Positive expiry, timing and larger static range |
| Music | Panel/APK path and firmware four-byte handler | Live one-byte mode selection/streaming and exact output behavior |
| Sleep/defaults | Firmware policy and reset semantics | Live delays, radio wake and persistence consequences |
| Vendor client | Composition presence and standard Tuya role | Product-specific actions or publications |
| Pixels | Internal logical 41-pixel engine | Physical mapping or external per-pixel control |
| Update path | Proprietary parser and image-region write route | Complete boot/integrity rules and runtime variants |

Acknowledgment, independent state readback and visible behavior
are separate evidence. The product schema contains more entries
than the demonstrated local control surface. Newly identified
static commands remain static until deliberately validated;
matching echoes cannot promote them to working features. Current
operational priorities and client/backend limitations are
maintained in [STATUS.md](STATUS.md) and [TECH.md](TECH.md).

## Source map

The access and state layouts above reconcile saved product/APK
artifacts with owned-device observations. APK identity is Olight
3.2.5, version code 239, SHA-256
`1fa9bddeb55ec39fba631e074be76f65ada37830735ae9a074f2aac83b6eecb9`.
The saved panel is `0.5.9_RsPYtSG2yNGWYhKJ`, bundle SHA-256
`024c0b833914dfd52102f4d96568ddda2be3223cf5a98b5b2c402eb18f1d8bf5`.
Some original traffic and app artifacts are private/ignored;
identities establish which artifacts were analyzed, without
publishing network keys. Firmware evidence has the image hash and
scope stated above; original findings are summarized in
[FIRMWARE.md](FIRMWARE.md).

Current code: [access codecs][access-code],
[lighting controller][lighting-code],
[vendor controller and catalog][vendor-code],
[daemon verification][daemon-code],
[Proxy transport][transport-code], [state persistence][state-code],
[GUI color conversion][gui-code], and
[provisioning](../src/spherosphere/provision.py).

Primary protocol sources: [Mesh Protocol 1.1][mesh-protocol],
[Mesh Model 1.1][mesh-model], [SIG Assigned Numbers][assigned],
[Mesh tests][mesh-tests], [Tuya vendor models][tuya-vendor] and
[Tuya BLE porting interface][tuya-port]. Their generic definitions
do not expand the device-specific evidence boundary above.

[capture]: REPORT.md
[provision]: GUIDE.md
[provision-analysis]: TECH.md
[models]: #composition-and-bindings
[lighting-live]: REPORT.md
[operations]: GUIDE.md
[dp-inventory]: #product-datapoints
[dp-codec]: #tuya-vendor-messages
[color]: #phone-color-and-brightness-mapping
[phone-mode]: #work-mode-dp-2
[motion]: REPORT.md
[timeout]: TECH.md
[elements]: #composition-and-bindings
[chip]: FIRMWARE.md
[fw-modes]: FIRMWARE.md
[access-code]: ../src/spherosphere/access.py
[lighting-code]: ../src/spherosphere/lighting.py
[vendor-code]: ../src/spherosphere/vendor.py
[daemon-code]: ../src/spherosphere/daemon.py
[transport-code]: ../src/spherosphere/transport.py
[state-code]: ../src/spherosphere/state.py
[gui-code]: ../src/spherosphere/gui/backend.py
[mesh-protocol]: https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/MshPRT_v1.1/out/en/index-en.html
[mesh-model]: https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/MMDL_v1.1/out/en/index-en.html
[assigned]: https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Assigned_Numbers/out/en/index-en.html
[mesh-tests]: https://files.bluetooth.com/wp-content/uploads/dlm_uploads/2025/02/MESH.TS_.p15.pdf
[tuya-port]: https://developer.tuya.com/en/docs/iot-device-dev/Porting-Guide-BLE?id=Kam0xjtz4n6e0
[tuya-vendor]: https://developer.tuya.com/en/docs/iot-device-dev/tuya-sigmesh-device-vendor-model-access-standard?id=K9pikwhoo3gux
