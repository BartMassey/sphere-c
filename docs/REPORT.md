# Spherosphere: from encrypted traffic to a local controller

Bart Massey and Codex. Updated November 6, 2026, for version 0.2.0.

Spherosphere now controls multiple Olight Sphere C lights from
Linux without the Olight app or cloud. A daemon owns the local
Bluetooth Mesh network; a desktop app provides enrollment,
naming, power, brightness, solid colors, and built-in effects.
The development setup has three enrolled Spheres, a dedicated
Mesh controller, and a separate adapter for ordinary Bluetooth.

The investigation has also moved beyond the phone application.
We recovered a Sphere C update image and analyzed its Telink
TC32 code. That explains much of the product's datapoint
behavior and its internal pixel map. It has not produced an
external command for uploading arbitrary scenes or animations,
and no modified firmware has been flashed.

This report describes how those results fit together. The
[Bluetooth reference](PROTOCOL.md) contains the exact
messages, tables, addresses, and evidence boundaries. The
[current status](STATUS.md) is the shorter continuation handoff.
The tested hardware is the USB-C **Sphere C**. Conclusions do
not automatically transfer to the earlier magnetic-charge Sphere.

## Encrypted traffic established the transport

The starting point was an Android Bluetooth snoop capture with
Mesh Network PDUs carried over a GATT Proxy connection. GATT was
the pipe, while Bluetooth Mesh supplied its own network and
application encryption. The absence of ordinary Bluetooth
bonding did not make application commands readable.

Packet lengths and timing suggested reads, configuration, and
vendor traffic, but a plausible message shape was not an opcode
decode. A captured Network ID could check a candidate NetKey;
it could not practically recover that key. Replaying traffic
would also run into sequence numbers and replay defenses.

HCI logging therefore did not bypass Mesh encryption. It became
useful alongside app analysis, key ownership, and known message
formats. The original trace contained 3,391 HCI frames over 346.7 seconds.
The [protocol reference](PROTOCOL.md#discovery-and-gatt) retains
its GATT identification and the limits of encrypted traffic
interpretation.

## The phone app provided a map, not a specification

The Olight APK contained the Thingclips/Tuya provisioning and
transport machinery. Its downloaded Sphere C panel supplied a
more product-specific schema, picker entries, and UI call sites.
Those were different strengths of evidence: a generic SDK could
implement a feature that this lamp never used, and a schema
entry did not prove a working firmware handler.

Ordinary power and lighting controls turned out to use standard
Mesh models. Product effects used the Tuya vendor model. In
particular, the built-in preset selector is DP102, not the
schema's generic DP6 `scene_data`. The selector has 32 schema
values, while the saved phone picker exposes 26. One is Color
Wheel, leaving 25 presets in the desktop Effects view.

Following the panel also established the HSV-to-HSL conversion
needed by the color wheel. Phone HSV value cannot simply be
copied into Mesh HSL lightness. Standard lighting state may
still settle to a quantized or converted value, so the controller
retains what each model actually reports.

The [DP inventory](PROTOCOL.md) and
[color audit](PROTOCOL.md#phone-color-and-brightness-mapping) separate
product call sites from generic library capability.

## Local provisioning removed the need for vendor keys

Bluetooth Mesh uses a NetKey for network security, an AppKey
bound to application models, and a DeviceKey for configuring
each node. Standard provisioning derives session material and
the DeviceKey through an elliptic-curve exchange, then supplies
the chosen network key and address. It does not require
extracting a factory private key from the lamp.

We used PB-GATT to give a Sphere C locally owned network state.
The first successful handshake was insufficient: early nodes
later rolled back to an unprovisioned state. A persistent run
completed the fuller standard configuration, including all
11 matching model bindings and Network Transmit configuration.
That tested sequence worked; we did not isolate one final
command as the unique persistence checkpoint.

The lamp then answered authenticated reads and retained network
membership across disconnect and button-off/wake. Authenticated
composition corrected another early mistake: IDs `0x130a` and
`0x130b` are HSL Hue and Saturation servers, not xyL. The Sphere C
does not advertise the real xyL Server, `0x130c`.

Power, Lightness, and HSL proved useful. CTL Temperature accepted
protocol operations without an obvious visible change in the
trial, so the GUI does not expose a temperature slider. Model
presence and acknowledgment are not substitutes for observing
a physical function. See the
[provisioning runbook](GUIDE.md#self-provisioning-and-direct-diagnostics) and
[lighting trial](REPORT.md).

## Firmware analysis clarified behavior

Static analysis of one Sphere C build helped distinguish
implemented behavior from generic SDK features. Its conclusions
apply to that build unless corroborated on hardware. No
modified image has been flashed. The public documentation
contains original explanations and protocol observations;
firmware bytes, recovered code and acquisition procedures are
not distributed.

## TC32 code narrowed the hardware and API questions

The image's coherent instructions and Telink strings establish
Telink's **TC32** software family. TC32 is a proprietary 32-bit
instruction set; this is not evidence of a 32-bit ARM image.
It does not identify the exact fitted MCU. Startup support for
several SPI flash identifiers also does not reveal which chip
or capacity is installed in a particular Sphere.

Ghidra with a TC32 processor module supplied the working
analysis environment. Function exports, recovered callbacks,
comments, annotation ledgers, and project checkpoints preserve
the work. REA was evaluated, but building a dedicated TC32
adapter was deferred; direct Ghidra scripts were sufficient for
these traces. Decompiler output was checked against instruction
listings where control flow mattered, and remains recovered
code rather than original source.

The [processor assessment](FIRMWARE.md#processor-identity)
keeps the ISA and exact-chip claims separate. The firmware
summary records the limits of the public findings.

## The firmware distinguished real DPs from schema promises

Tracing the registered application callback and ordinary DP
write dispatcher explained several product features:

- DP7 and DP101 update the same countdown. A callback decrements
  it and toggles power on expiry. This is not simply an
  unconditional shutdown timer.
- DP8 carries a packed four-byte music state, applied in mode3.
  Its presence does not establish a finished desktop music mode.
- DP102 selects a built-in preset, powers the light on, and
  chooses color mode. Its 32-entry mapping includes aliases.
- DP103 produces battery reports.
- DP104 participates in persistent sleep-policy state.
- DP106 restores broad defaults, including initial button-slot
  selections; it is not merely a custom-scene upload command.

Work mode is separate from the internal rendering effect and
from the hardware-button slot. Ordinary typed DP2 writes have
no action in the traced dispatcher; a separate one-byte vendor
command is the mode-setting path. Existing two-step preset
activation works through the subsequent DP102 write, so a
successful sequence does not prove that the typed DP2 step did
anything. That remains an application simplification to make.

The button behavior also became less mysterious. DP102 reaches
slot-update logic under the appropriate button states, and
preset state participates in deferred settings persistence.
This supports the interpretation of Factory Colors as four
button-accessible selections, not arbitrary uploaded content.
Exact app interaction with those slots still needs focused
physical-button and persistence validation.

Conversely, generic DP6 `scene_data` has no action in the traced
ordinary dispatcher. Other schema properties likewise cannot
be promoted to implemented device features just because the
APK declares them. Standard model callbacks are separate, and
absence from this dispatcher is not a universal proof about all
protocols or firmware versions. The
[handler audit](FIRMWARE.md#application-routing-and-dp-writes) records
those bounded conclusions.

## The internal pixel map did not reveal an upload API

The firmware corrected an important physical assumption. The
Sphere C manual specifies **41 colored LEDs and 67 white LEDs**,
not 64 RGB pixels arranged in seven rings and a cap. The latter
was a recollection, not an established topology. FCC photos do
not resolve the complete physical indexing.

Recovered code has a color-pixel setter bounded to indices
0 through 40 and two 41-entry buffers. A coordinate table maps
all 41 logical indices onto an 11-by-5 virtual canvas with
padding cells. The virtual map does not establish physical
orientation, ring layout, or externally addressable zones.
Even internal code that paints one indicator pixel does not
supply a remote pixel-selection command.

We followed the likely DP, clock/vendor, and FD50 transfer
leads toward those buffers. No validated path for uploading
arbitrary pixel frames or animation programs emerged. Optional
SDK DP-getter and command hooks are cleared during normal
initialization; the reference/store audit found no registration
in the captured build. That supports dormant-hook conclusions
for this image, with retention and computed paths still bounded
caveats, not a claim about every possible version.

The APK's generic scene compressor is partly understood, but
its existence does not establish that the Sphere C consumes its
output. A format alone is insufficient without a reachable
firmware handler and a validated transaction. See the
[pixel mapping](FIRMWARE.md#internal-pixels-and-virtual-coordinates) and
[scene/flash analysis](FIRMWARE.md).

Firmware modification remains a possible later project. The
OTA writer's `0x30000` limit leaves 11,872 bytes beyond this
particular image, but that arithmetic is not a safe code-growth
budget. Boot acceptance, headers, integrity checks, alignment,
and usable flash regions are unresolved. No custom image has
been flashed and no custom-scene support is implemented.

## Firmware evidence fixed a host-side readback bug

One practical result required no device modification. Scene
reads appeared to return only an empty acknowledgment or DP7.
We had treated the omissions as a possible firmware limitation.
The recovered callback instead predicted a fixed status burst:
a bare mode byte, then DPs 7, 101, 102, 103, and 104 as separate
messages, largely independent of the requested DP list.

Read-only probes gave each burst five seconds. All four request
forms on each of three Spheres returned DP102. It arrived about
358–426 ms after sending; the full burst ended around
578–676 ms. The Python collector had been returning after a
100 ms settle following the first response, before the scene
arrived. Waiting for the requested records fixed the omission.

Mode needed context as well. The first byte `01` can mean mode1
in a stock READ snapshot, but looks like an acknowledgment in
isolation. The decoder now requires the complete ordered
record/type signature before treating that leading byte as
mode0..4. Partial bursts can report a scene without establishing
mode; write acknowledgments are not interpreted as readback.

The GUI can therefore recover both current scene and active
mode after startup or a device switch, without assuming a saved
preset is still rendering. Saved metadata remains a labeled
Last known fallback. The
[readback review](FIRMWARE.md#work-mode-and-reported-state)
connects the static callback to the live timing evidence.

## Multiple lights made the host's Mesh role important

A GATT Proxy client was enough to establish multi-node control
through one shared connection. For the host to be a central
Mesh participant and relay, we added a native BlueZ Mesh backend.
There are now two transports and three service profiles:
shared-adapter proxy, dedicated-controller Mesh, and Mesh with
a second ordinary adapter.

Trying to mix ordinary Bluetooth and Mesh on the same controller
exposed BlueZ transmit and recovery problems. The working host
setup gives the motherboard radio exclusively to system
`bluetooth-meshd` using generic I/O. A USB adapter remains with
ordinary BlueZ for scanning and GATT enrollment. Dedicated Mesh
control and concurrent USB discovery passed; GUI enrollment
then added a third lamp, CAT, alongside ARI and BEN.

The native host is configured with Relay enabled. This establishes
working Mesh participation and control, not a controlled proof
of multi-hop forwarding through that host. Normal audio
coexistence and broader enrollment reliability also remain
separate validation tasks. The
[BlueZ dossier](TECH.md#shared-controller-bluez-findings) and
[dedicated-controller trial](TECH.md#shared-controller-bluez-findings)
retain upstream issue evidence and restoration details.

Sequence state remains essential. The application protects its
GATT source's cursor with durable state and an exclusive lock.
The native BlueZ host uses a different source and BlueZ-owned
sequence/replay storage. Moving files or changing transports
must not clone or roll back either identity.

## The desktop application now reflects the actual light

The GUI manages several named Spheres, performs automatic
compatible-device enrollment, and selects the current source
when switching devices. Power-off produces a black global
preview. An active preset uses its recorded still or animation;
Color Wheel uses the color controls. Gallery tiles have fixed
preview brightness, while the top preview follows the selected
light's brightness once it is known.

Recording sessions classified the 25 gallery presets into six
steady patterns and 19 animations. The packaged photographic
WebP assets are CC0 recordings of the lamp, not copied phone-app
artwork. Original videos and manifests remain private for later
processing. Focus, bloom, and color calibration still limit how
closely those previews match the physical object; they are
illustrations rather than a live or calibrated rendering.

Spherosphere 0.2.0 also has a supervised user service and shared
XDG paths. Config, Mesh state, and the socket no longer depend
on where the source checkout lives. On the development host,
the state was moved under locks with byte equality checked
before activation, preserving all three device names and Mesh
identities. A wheel-installed GUI was then exercised from
outside the checkout after the directory was renamed.

The version 0.2.0 checkpoint passed 487 automated tests, covering
codecs, state ownership, transport/service behavior, paths, and
Qt/QML behavior. Separate live checks confirmed current scene
and mode reads on ARI, BEN, and CAT and the installed GUI's
source selection. A test count does not establish device
behavior outside those recorded trials.

## What remains

The next priorities are now, in order:

1. Understand the device's music mode and implement useful
   audio-reactive control in the daemon and desktop app.
2. Provide a secure REST API for Internet access by an Android
   app or other client, with authentication, authorization, and
   encrypted transport rather than exposing the local socket.
3. Modify firmware to support uploading and playing custom
   scenes and animations, with matching software support.

These are future capabilities, not features of version 0.2.0.
Music has a traced firmware handler but still needs live
behavioral validation. The daemon currently exposes a private
Unix API, not an Internet REST service. Custom content has an
internal renderer to build on, but no validated stock upload
interface or tested modified firmware.

Preset activation and button-slot persistence, countdown
expiry/cancellation, broader controller and relay validation,
and original-Sphere compatibility remain additional work.
Firmware changes require their own update/recovery validation;
static image headroom is not a safe flashing plan.

For installation and enrollment, use the
[user README](../README.md). For the detailed
[technical roadmap](TECH.md#future-work) and current priorities,
read [TECH](TECH.md) and [STATUS](STATUS.md).
