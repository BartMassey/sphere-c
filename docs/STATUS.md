# Spherosphere status

Updated 2026-11-06. Source version **0.2.1**.
This file is the current public handoff. Private research notes
and original history are retained separately from public Git.

## What works

- Linux control of USB-C Olight Sphere C devices without the
  Olight app or cloud, using locally owned Bluetooth Mesh keys.
- Multiple named devices in one network and GUI selector.
- Power, logarithmic brightness, HSV color controls, and
  25 built-in presets: six patterns and 19 animations.
- Current preset and work-mode readback, including after a
  daemon restart or switching devices.
- Recorded WebP previews, with fixed-brightness gallery tiles
  and a scene/brightness-aware global preview; off is black.
- Automatic compatible-device enrollment, immediate discovery
  display, unique default names, rename, and local forgetting.
- A supervised user service and shared XDG config/state/socket
  paths independent of the source directory.
- GATT Proxy and native BlueZ Mesh transports. Dedicated Mesh
  plus a separate ordinary USB adapter is live-validated.
- Standalone DeviceKey composition inspection and authenticated
  GATT Proxy filter-status logging for diagnostics.

The public starting point is the [user README](../README.md).
The [technical report](TECH.md) covers profiles, CLI tools,
state, and development. The updated [report](REPORT.md) tells
how the app, captured firmware, and Mesh work fit together.
The maintained set is GUIDE, TECH, PROTOCOL, FIRMWARE, REPORT,
and STATUS; `docs/README.md` is its compact index. Private
working notes are not required to understand these documents.

## Host installation and last live checkpoint

Checkout: `/usr/local/src/spherosphere`.
Repository: `BartMassey/spherosphere`.
The Python package is `spherosphere`; primary commands are
`spherosphere` and `spherosphered`. Maintenance/research entry
points still exist: reducing their public PATH exposure was
recommended, but has not been implemented.

The last verified user service is `spherosphere.service`,
enabled in split mode. Its installed environment is
`~/.local/share/spherosphere/venv`, independent of the checkout.
The branding migration installed the standalone runtime before
its source version metadata was bumped to 0.2.0. A 0.2.0 wheel
was subsequently built and checked; do not assume a deployment
upgrade solely from the source version.

| Setting | Last verified value |
|---|---|
| Config | `~/.config/spherosphere/daemon.toml` |
| Application state | `~/.local/state/spherosphere/state.json` |
| Socket | `$XDG_RUNTIME_DIR/spherosphere/control.sock` |
| Native Mesh controller | Motherboard `F8:AC:65:82:08:83` |
| Ordinary BlueZ controller | USB `B0:82:E2:2B:FD:35` |
| Native host | `0x0130`, Relay enabled |

Resolve adapters by MAC rather than assuming stable `hciN`
indices. System `bluetooth-mesh.service` owns the dedicated
controller through `/usr/local/libexec/spherosphere-controller`;
ordinary BlueZ remains available on USB. Mesh enrollment on
this host is explicitly opted in and remains experimental.
No controlled multi-hop relay proof or normal-audio trial has
been completed.

| Sphere | Primary address | Last installed-GUI readback |
|---|---|---|
| ARI | `0x012a` | Aurora, mode 1 |
| BEN | `0x0131` | Flame, mode 1 |
| CAT | `0x0137` | Color Wheel, mode 1 |

These are recorded observations, not assumed current settings.
BEN and CAT were updated by the owner to 0.5. ARI is the original
working Sphere C; its saved firmware-version field is null.
Do not infer pairing mode from cached BlueZ service metadata.

The old checkout state was moved with both locations locked
and byte equality checked before activation. Device names,
keys, addresses, and sequence values were retained; BlueZ's
own identity store was not moved or reset. Legacy user units
and config are archived under
`~/.config/spherosphere/legacy-installation`. Private migration
and installed-GUI records are in the XDG state directory.
Confirm service health before new live work rather than
assuming that a previously running process is still alive.

## Enrollment and GUI behavior

Current enrollment accepts compatible Sphere C firmware 0.5.
Use the [full enrollment instructions](../README.md#enroll-a-sphere):
Bluetooth off on phones with Olight, Sphere on via its button,
three rapid presses until green blinking, then **+ → Enroll nearby
Spheres** in the GUI. Do not enroll through desktop Bluetooth
pairing settings. A physical pairing-mode action is required
before taking a previously owned Sphere into another network.

The GUI assigns names automatically and can rename afterward.
Closing the dialog stops further attempts without discarding
enrolled devices. **Forget** removes local inventory only; it
neither resets a Sphere nor revokes its network membership.
Rows are font height plus 6 pixels, vertically centered, with
no inter-row gap. Both management lists remain scrollable.

Current mode overrides remembered scene metadata when choosing
a preview. Mode0 uses Light; mode 1 uses its preset or Color
Wheel; modes2–4 use neutral previews and mode labels. Cached or
saved fallbacks remain distinguishable from fresh readback.
The source is selected on device switching without hijacking
subsequent manual navigation.

## Firmware findings and evidence limits

The analyzed Sphere C update image is 184,736 bytes and contains
readable TC32 code/data. It is not a verified full flash dump.
The exact fitted MCU and total flash capacity are unresolved. No modified firmware has been flashed.

The unchanged image, Ghidra project, scripts, annotations,
exports, and checkpoints remain in ignored local storage.
Private working material records their actual workspace paths.

Ghidra 12.2 plus a TC32 module supplied the working analysis.
REA was evaluated; a TC32 adapter contribution is deferred.
Preserve the original image. Do not open the same Ghidra project
concurrently in GUI and headless mode. The
[firmware ledger](FIRMWARE.md)
summarizes the public conclusions.

Established in the captured image:

- DP7/101 share a countdown; its expiry toggles power.
- DP8 applies a packed music state only in the appropriate mode.
- DP102 selects a built-in preset, powers on, and enters mode 1;
  it also reaches hardware-button slot-update logic.
- DP103 reports battery; DP104 is persistent sleep policy;
  DP106 broadly restores defaults and initial slot selections.
- Typed DP2 writes have no action in the ordinary dispatcher;
  a separate one-byte vendor path sets mode.
- The internal color API uses 41 pixels and two 41-entry buffers,
  with a recovered 11x5 virtual coordinate map. Physical layout
  and externally selectable pixel indices remain unverified.
- The manual specifies 41 colored and 67 white LEDs. The earlier
  64-RGB/ring-layout recollection was not confirmed.
- DP6 has no action in the traced ordinary dispatcher. No
  validated arbitrary scene/frame/animation upload path was
  found through the DP, clock/vendor, FD50, or optional-hook
  leads. Conclusions are bounded to identified paths/build.
- OTA transfer reaches image-region flash writes. Its 192-KiB
  writer limit leaves 11,872 bytes above this image, but usable
  code-growth space and boot/integrity acceptance are unknown.

All API findings belong in the
[Bluetooth reference](PROTOCOL.md), with supporting
[DP](FIRMWARE.md#application-routing-and-dp-writes) and
[pixel](FIRMWARE.md#internal-pixels-and-virtual-coordinates) evidence.

## Scene and mode readback resolution

The READ callback emits a fixed burst: bare mode, then typed
DPs7,101,102,103,104. Requested ID order does not make a missing
scene magically readable. The old collector returned after a
100-ms settle following first DATA; DP102 actually arrived
358–426 ms after sending in the timing trials.

All 12 combinations of four request forms across three lamps
returned DP102 when observed for five seconds. The collector
now waits for requested records or the full snapshot. Work mode
is decoded from the leading byte only when the complete ordered
record/type signature confirms its context. Isolated `01`
acknowledgments and incomplete bursts do not establish mode.
No typed DP2 record is invented. Raw records and decoded bare
mode remain separate in API responses.

Private evidence: `state/captures/scene-read-bursts-20261105.json`
in the checkout; later live work-mode and installed-GUI records
are in the XDG state directory. The
[readback review](FIRMWARE.md#work-mode-and-reported-state)
links static control flow to those live results.

## Remaining work

The owner set these project priorities on 2026-11-06, in order:

1. **Music mode:** validate the mode transition and DP8 behavior
   on the light, then implement audio-reactive control, update
   scheduling, lifecycle behavior, and GUI support.
2. **Secure remote REST API:** let an Android app or another
   remote client operate the daemon over the Internet with
   authenticated, authorized, encrypted access. Reuse daemon
   ownership/serialization; keep Mesh secrets on the host. The
   current private Unix API is not a remote REST service.
3. **Custom scenes and animations:** modify firmware to provide
   an upload/playback interface, then support it in the daemon
   and app. No stock upload path or usable modified image has
   been established. Resolve format, storage, boot/integrity,
   and recovery questions before live firmware changes.

[TECH's roadmap](TECH.md#future-work) records the implementation
questions; the [user README](../README.md#future-work) presents
these as planned features. Music is no longer deferred.

Other maintenance and validation work remains useful, without
replacing that priority order:

- Simplify preset activation around DP102; remove reliance on
  ineffective typed DP2 writes. Validate active physical-button
  preset-slot updates and persistence.
- Keep countdown GUI work behind real positive-expiry and
  cancellation checks. The firmware toggles power, so a
  shutdown-only interpretation is incorrect.
- Reduce public command clutter while retaining CLI diagnostics
  and maintenance/research modules.
- Improve preview capture quality if useful; original videos
  are retained privately. No more silhouette-mask work is
  planned. Preserve fixed gallery brightness and the global
  preview's existing nonlinear brightness mapping.
- Broaden dedicated/split enrollment, audio-coexistence,
  reconnect, and controlled multi-hop validation. Keep upstream
  issue evidence in the [BlueZ findings](TECH.md#shared-controller-bluez-findings).

Lockout GUI and further mode2/4 investigation remain deferred.
Original magnetic-charge Sphere support is unvalidated and
must not reuse Sphere C assumptions. Earlier completed
investigations are historical, not a replacement for this list.

## Validation and continuation rules

The 0.2.1 source passed **501 tests**, including composition
inspection and encrypted proxy-status regression coverage.
No new live hardware validation was performed for these changes.

The 0.2.0 checkpoint passed **487 tests**. Its wheel metadata,
renamed desktop/service assets, and artifact exclusions were
checked. The installed Qt app was exercised outside the source
checkout after its rename: all three device selections showed
the expected scene/mode and matching preview. Those checks sent
reads, not lighting Sets. No fresh hardware tests were run for
this documentation rewrite.

The public documentation is a reviewed set of nine Markdown
files, including asset provenance. Historical working notes,
recovered code, extracted tables and acquisition procedures
are excluded. Private research and the original history are
retained separately in ignored storage. Runtime source and
hardware were not changed by this cleanup.

Use `./.venv/bin/pytest -q` after relevant code changes. Qt and
local-API tests require temporary Unix sockets; sandbox denial
is not itself a source regression. Do not rerun radio experiments
as a substitute for identifying the task's actual scope.

Never publish keys, phone exports, raw firmware or private
captures. Never clone or roll back used Mesh identities. Stop
state owners before migration or backup and preserve sequence
progress. Source docs and decoded evidence are separate from
raw acquired artifacts. [TECH](TECH.md) provides the maintained
source map. Private working notes remain separate from the published
repository; hypotheses are not substitutes for validated facts.
