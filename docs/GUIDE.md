# Spherosphere user and operator guide

This is the detailed guide to installing, enrolling and operating
Spherosphere. The [quickstart](../README.md) covers first use;
[TECH](TECH.md) describes architecture and the local API;
[PROTOCOL](PROTOCOL.md) specifies Mesh and vendor behavior.
[FIRMWARE](FIRMWARE.md) records static image analysis,
[REPORT](REPORT.md) explains the investigation, and
[STATUS](STATUS.md) gives the current handoff.

## Compatibility and prerequisites

The tested light is the USB-C **Olight Sphere C**, running
firmware **0.5**. Automatic enrollment checks the product UUID
and live firmware record and rejects unsupported candidates.
Spherosphere does not update firmware. Complete any required
update with Olight before beginning local enrollment.

The earlier magnetic-charge Sphere has not been validated.
Standard Mesh controls might be reusable if its services and
composition match, but the effects, vendor commands and recovery
behavior here are Sphere C-specific. Begin any investigation
with passive advertisements. Use separate state and that
light's own keys for authenticated testing; do not reset or
provision an original Sphere merely to try the GUI.

Use Linux, Python 3.12 or newer, Git, ordinary BlueZ, a working
Bluetooth Low Energy adapter and a desktop session. Python's
`venv` module must be available; Debian packages it as
`python3-venv`. Persistent service instructions assume a systemd
user manager. The dedicated Mesh profiles additionally require
system `bluetooth-meshd` and an exclusive controller.

## Install and launch

Install in a persistent virtual environment as your normal user:

```sh
git clone https://github.com/BartMassey/spherosphere.git
cd spherosphere
python3 -m venv ~/.local/share/spherosphere/venv
. ~/.local/share/spherosphere/venv/bin/activate
python -m pip install '.[gui]'
spherosphere-service install --profile proxy --enable-now
spherosphere
```

Installation fetches dependencies, including a pinned Git
package. Keep the environment available for the service's
lifetime; a regular installation does not need the checkout
at runtime. From a later terminal, activate that environment
and run `spherosphere` again.

For development, use an editable installation:

```sh
python3 -m venv .venv
./.venv/bin/pip install -e '.[gui,dev]'
```

The checkout's `./bin/` launchers are also available. Start
`./bin/spherosphered` in a terminal only after stopping any
installed application daemon that owns the same state. A
foreground daemon and the systemd user service must not run
against the same state simultaneously.

### Desktop integration

Packaging includes `spherosphere.desktop` and a scalable icon.
A virtual-environment installation keeps those files inside
that environment, which desktop shells usually do not search.
A distribution package or explicit per-user installation must
place the launcher and icon under the corresponding
`XDG_DATA_HOME` application and icon directories and make
`spherosphere` available on the launcher's `PATH`.

The app follows the desktop palette, supports narrow resizable
windows and provides keyboard focus and accessible labels.
Its illustration and icon are original project assets; recorded
previews are CC0. See the
[asset provenance](../packaging/desktop/ASSETS.md).

## Enroll a Sphere

Charge the light enough to turn on, then follow this sequence:

1. Disable Bluetooth on every phone with Olight installed.
   Keep it disabled throughout enrollment.
2. Turn the Sphere on using its button.
3. Press its button three times rapidly. Confirm green blinking,
   which indicates pairing mode.
4. In Spherosphere, click **+**, then **Enroll nearby Spheres**.
5. Wait for the compatible light to show **Enrolled**. Discovery,
   compatibility checking and configuration run automatically.
6. Stop enrollment, rename the light if desired, and select it
   in the main window.

Do not pair through desktop Bluetooth settings. Spherosphere
performs Mesh enrollment itself. The physical pairing gesture
also permits a previously enrolled Sphere C to join another
network; its former controller will no longer control it.

The discovery list shows **Found** before compatibility checks,
then the current setup or recovery stage. New discoveries remain
visible while another light is being configured. If pairing mode
expires, repeat the button gesture and open another window.

Unique default names such as `Sphere 123C04` are saved with
recovery information before network ownership changes. No
naming prompt interrupts provisioning. Closing the dialog stops
further attempts but allows an already-started operation to
finish safely. Enrolled lights and names remain saved.

Pre-provisioning connection failures receive up to three
attempts per window. After network data might have reached the
light, recovery uses its saved DeviceKey and reserved address
instead of creating another identity. A later enrollment window
can retry unfinished configuration. Ordinary light controls
pause for the enrollment window.

### Automatic enrollment and local forgetting

The **+** dialog lists current lights. Rename with Save or
Enter. Names must be nonempty, trimmed, at most 64 characters,
and unique without regard to case; explicit `0x` address strings
cannot be names. Rename and Forget are unavailable during an
active enrollment window.

**Forget** removes a light from active inventory and future
connection selection. It does not reset the light or revoke
its Mesh membership. Retired addresses, keys and recovery
records remain private so allocations cannot be reused.
Use the physical pairing gesture to enroll that light again.
Never rewind allocation or sequence state to reclaim a record.

### CLI enrollment

Proxy mode supports enrollment without an experimental flag:

```sh
spherospherectl enroll --seconds 120
spherospherectl enrollment status
spherospherectl enrollment stop
spherospherectl devices
spherospherectl rename 0x0131 CYR
```

Replace the example address with one returned by `devices`.
The default window is 120 seconds; `--seconds` accepts 1–3600.
A foreground proxy daemon can also use `--enroll-seconds 120`.
The CLI automatically assigns names unless a custom API client
requests candidate naming. See [TECH](TECH.md) for that contract.

### Experimental Mesh enrollment

Mesh mode disables enrollment by default. Deliberate testing
requires `--experimental-enrollment`; in a split setup,
`--adapter` must identify the ordinary second controller.
The current implementation pauses host relay advertising for
the entire enrollment window, even with separate radios, and
restores its settings afterward. Proxy mode instead closes its
GATT proxy connection until the window finishes.

A split USB reenrollment of BEN completed using durable-key
recovery after a GATT error, with subsequent reads from both
lamps. This is limited evidence, not general enrollment
reliability. The automatic GUI flow has offline socket and Qt
smoke coverage; full live batch enrollment remains unproven.
CAT's first attempt with the older naming pause failed before
Provisioning Data, and its automatic-flow retry has not been
live-validated. Check [STATUS](STATUS.md) before further trials.

## Manage Spheres

Choose a name in the main selector. All controls and refreshes
target that device. Selection reads its current source and
navigates to Light for static Color or the matching Effects
category and tile for a preset. Subsequent manual browsing is
preserved. Replies for a previous selection cannot overwrite
the current light's display.

Only the daemon owns Bluetooth, keys, device names, sequence
state and command serialization. The GUI and CLI communicate
through its private local socket; they never open Mesh state.

## Views

### Light

Power is a separate acknowledged control. Brightness sends
only after slider release and never sends zero. The GUI uses
a logarithmic range from the light's roughly 3% minimum active
level to full output. Its percentage is the slider position,
not a linear percentage of the Mesh value.

Choose **Color** to select static Color Wheel mode and apply
hue, saturation and brightness. In color/effect mode, brightness
uses an HSV update carried through Mesh HSL, preserving hue,
saturation and the selected effect. Outside a reported color
mode it uses Mesh Lightness. The GUI converts between HSV and
the transport fields; HSV brightness is not mathematical HSL
lightness. Controls accept the light's returned state rather
than assuming the requested value persisted.

Battery percentage is read from vendor DP 103 when decodable.
It can remain hidden if replies contain no usable battery data;
that does not disable ordinary controls.

### Effects

The catalog shows 25 built-in presets: six steady **Patterns**
and 19 **Animations**. Both categories support search and
Return/Space activation. Color Wheel is selected in Light;
it is an internal static mode rather than an animation.
Custom scene or animation uploads are not supported.

Gallery previews stay at a fixed 50% display brightness.
Animations play on hover, keyboard focus or selection while
Effects is open. The compact preview beside Power follows the
selected light's scene and brightness and goes black when off.
Missing media uses a labeled illustration. Previews are recorded
examples, not live images or calibrated luminance measurements.

Selecting a tile invokes the legacy mode-then-effect activation
sequence. The daemon may retry only the effect selection once
if its response times out; the GUI never repeats the whole
activation automatically. It independently reads the effect
afterward, including after partial failure. An acknowledged
mode write alone does not establish that mode changed; see
[PROTOCOL](PROTOCOL.md) for the firmware-specific semantics.

### Current state and last-known previews

Fresh status reports determine the scene and work mode together.
Mode 0 uses the light preview; mode 1 displays the reported
preset or static Color. Modes 2–4 show a mode label and neutral
preview. A selected effect ID alone does not prove it is active.

The daemon validates the complete stock status burst before
interpreting its bare leading work-mode byte. It also saves each
light's acknowledged selection as fallback metadata. When
current mode is unavailable, the GUI labels that fallback
**Last known**. Fresh values take precedence; contradictory or
uncertain writes invalidate the saved scene.

Physical-button or other-client changes can make saved metadata
stale. With neither readable nor saved selection, choose an
effect once to establish its preview. Displaying fallback state
does not send a lighting command.

### Settings

Settings shows the socket, connection generation, battery,
model-derived capabilities and last connection failure.
**Copy Diagnostics** copies a redacted JSON summary with no
keys or sequence state. A model capability indicates support
in saved composition, not verified visible behavior.

Temperature remains an experimental CLI operation: live changes
had no obvious visible effect. Countdown and microphone-driven
music controls are absent from the GUI. Understanding and
implementing music mode is now the first project priority;
see [future work](TECH.md#future-work). A secure remote REST API
and custom-content firmware follow it on that roadmap.

## Daemon profiles

| Profile | Transport | Ordinary adapter |
|---|---|---|
| `proxy` | BlueZ GATT Proxy | Shared adapter; optional selection |
| `mesh` | Dedicated Mesh advertising bearer | Not required |
| `split` | Same Mesh bearer | Required separate adapter |

`split` is a service-installation profile, not another backend.
Both Mesh profiles run `mode = "mesh"`. Proxy mode needs ordinary
`bluetoothd` and no system Mesh daemon. Mesh mode joins through
system `bluetooth-meshd`, which exclusively owns its controller
with generic I/O. Ordinary `bluetoothd` can serve a second radio.

`--adapter MAC|hciN` always selects the ordinary BlueZ adapter
for scans, GATT and enrollment. It never selects the system Mesh
controller. Prefer hardware addresses because hci indices can
change. An unavailable explicit adapter fails startup rather
than silently choosing a different one. Without selection,
ordinary operations use BlueZ's default adapter.

Foreground examples, after the required controller setup:

```sh
spherosphered --mode proxy --adapter ORDINARY_ADAPTER_MAC
spherosphered --mode mesh
spherosphered --mode mesh --adapter USB_ADAPTER_MAC
```

`--backend proxy|bluez` remains a compatibility alias for
`--mode proxy|mesh`; conflicting choices are rejected.

## Persistent dedicated controller setup

Install Debian's `bluez-meshd` system package if necessary.
Reserving a controller changes system configuration and requires
administrator access. Identify the desired hardware address:

```sh
./bin/spherosphere-bt controllers
sudo python3 scripts/install_mesh_controller.py install \
  --controller MESH_CONTROLLER_MAC
```

The equivalent checkout helper is
`./bin/spherosphere-bt mesh-controller install --controller MAC`.
The installer creates a root-owned launcher and a system
`bluetooth-mesh.service` drop-in. It replaces only a recognized
older temporary test override; unrelated configurations are
not overwritten.

At startup the launcher resolves the saved MAC to its current
hci index, checks LE support and powers off only that controller
before executing the generic Mesh backend. Missing or ambiguous
hardware fails. The dedicated controller disappears from
ordinary BlueZ controller lists while Mesh owns it; the other
adapter and `bluetoothd` stay available. The setup survives
reboot and retries recoverable initialization failures.

After stopping the previous application daemon, install the
appropriate user profile:

```sh
spherosphere-service install --profile split \
  --adapter USB_ADAPTER_MAC --enable-now
```

Use `--profile mesh` without `--adapter` when no ordinary second
radio is needed. All shipped Mesh profiles disable enrollment.
Add `--experimental-enrollment` only for deliberate investigation.
The user service installer never changes system controller
ownership itself.

To release the controller back to ordinary Bluetooth:

```sh
systemctl --user stop spherosphere.service
sudo python3 scripts/install_mesh_controller.py release
spherosphere-service install --profile proxy --enable-now
```

Release disables the system Mesh service, retains its managed
drop-in as `.disabled` and powers on the saved controller.
It does not reset lamps or revoke keys. Installed distributions
also ship the launcher and installer under
`share/spherosphere/systemd`.

### Temporary dedicated-controller setup

For a temporary investigation, the checkout helper provides a
runtime-only handoff that expires on reboot:

```sh
./bin/spherosphere-bt mesh-only start \
  --controller MESH_CONTROLLER_MAC
./bin/spherosphered --mode mesh --adapter USB_ADAPTER_MAC
```

Omit the ordinary adapter if none is needed. Stop the foreground
application daemon before restoring ownership:

```sh
./bin/spherosphere-bt daemon-stop
./bin/spherosphere-bt mesh-only restore
```

The old restore helper refuses to undo a permanent assignment.
For an installed service use the persistent release procedure.
Controller handoff never calls for restoring cryptographic state
or rolling back sequence cursors.

## systemd user service

Run `spherosphere-service install` without `sudo`. It creates the
user unit and private shared configuration and records the
resolved absolute daemon executable. It preserves recorded
state, socket and executable paths when later profile changes
omit them. Installation rewrites profile settings, including
verbose logging and enrollment opt-in; review the resulting
configuration when changing profiles.

Use `--state /absolute/path` to retain an existing state file in
place, `--socket /absolute/path` for a custom socket, or
`--executable /absolute/path/to/spherosphered` to select an
installation explicitly. These options do not migrate or copy
network state. Stop its current owner before `--enable-now`.

Without `--enable-now`, start the installed unit yourself:

```sh
systemctl --user enable --now spherosphere.service
```

The unit starts with the user manager at login. Unattended
operation without login requires administrator-configured user
lingering; the installer does not change that policy. Failures
restart after three seconds; clean exits stay stopped. Its
umask is `0077`, and writable directories are restricted to
private state and runtime locations.

Edit configuration, then restart to apply changes:

```sh
systemctl --user restart spherosphere.service
systemctl --user status spherosphere.service
journalctl --user -u spherosphere.service -f
```

Unit-file changes also require `systemctl --user daemon-reload`.
The service helper provides `status`, `restart`, `stop` and
`logs` subcommands as conveniences.

## BlueZ setup and state

### Settings and local data

| Purpose | Default path |
|---|---|
| Shared configuration | `~/.config/spherosphere/daemon.toml` |
| Keys, names and controller state | `~/.local/state/spherosphere/state.json` |
| User unit | `~/.config/systemd/user/spherosphere.service` |
| Local socket | `$XDG_RUNTIME_DIR/spherosphere/control.sock` |

These paths are independent of the source checkout.
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` override their roots.
Without `XDG_RUNTIME_DIR`, the socket is `runtime/control.sock`
under the private state directory. `SPHEROSPHERE_CONFIG`,
`SPHEROSPHERE_STATE` and `SPHEROSPHERE_SOCKET` override shared
defaults; explicit command-line options take precedence.
The GUI accepts `--socket /path/to/control.sock` too.

Configuration uses flat TOML settings:

```toml
mode = "mesh"
adapter = "AA:BB:CC:DD:EE:FF"
experimental_enrollment = false
verbose = false
```

Optional `state` and `socket` settings must be absolute paths.
The installer records custom paths in both shared configuration
and the unit's generated drop-in.

### Identity, sequence state and backups

Retain the private application state. It contains the network
keys, DeviceKeys, names, allocations, direct-controller identity
and durable sequence cursor needed to control enrolled lights.
Only one process may own it. Never run a copied state as another
simultaneous controller or restore an older cursor for an
identity that has transmitted: sequence reuse can reuse an
encryption nonce.

Mesh mode allocates a separate fresh host unicast identity and
saves its attachment token under `bluez_mesh` in application
state. Existing lamps need no reset or reenrollment. BlueZ owns
that host's sequence cursor, replay protection and node database
under `/var/lib/bluetooth/mesh`. The direct GATT controller keeps
its own source identity and sequence domain in application state.

Back up application state and the BlueZ Mesh database together
while their owning daemons are stopped. Preserve their matching
identities, token and sequence history. A filesystem move or
controller handoff does not create a new source identity.
Old checkpoints are forensic evidence, not safe restore images.
Recovery from badly stale sequence state requires a fresh,
nonoverlapping controller identity or reprovisioning; do not
hand-edit cursors to make a connection work.

### Moving an older installation

Stop and disable the older `sphere-mesh.service` and stop any
foreground state owner. Keep the system Mesh daemon running for
an ordinary application-state migration; its database is not
being moved. Then use:

```sh
spherosphere-service migrate-state /path/to/old/state/sphere_state.json
spherosphere-service install --profile split \
  --adapter USB_ADAPTER_MAC --enable-now
```

Choose the profile suitable for your controllers. Migration
locks both locations, validates and moves the source without
changing keys, names or sequence values. It refuses a live owner,
an existing destination or a cross-filesystem identity copy;
it does not merge networks. Use `--destination` for a different
target. To keep the existing file in place, skip migration and
pass `--state PATH` when installing instead.

## Command-line control

`spherospherectl` uses the daemon socket and does not need keys:

```sh
spherospherectl health
spherospherectl devices
spherospherectl --device ARI status
spherospherectl --device ARI control read
spherospherectl --device ARI control on
spherospherectl --device ARI control off
spherospherectl --device BEN control read
spherospherectl --device ARI lighting lightness
spherospherectl --device ARI lighting hsl
spherospherectl --device ARI lighting lightness=32768
spherospherectl --device ARI lighting hsl=32768,0,65535
spherospherectl --device ARI vendor-read 2 102
spherospherectl effect list
spherospherectl --device ARI effect get
spherospherectl --device ARI effect select aurora
spherospherectl --device ARI effect activate aurora
```

Replace example names with your own. `--device` precedes the
command and accepts a unique saved name or primary address.
With more than one active device, omitting it yields
`device_required`. Global health, inventory, enrollment and
effect-catalog operations need no target.

`effect select` writes the selected effect; `effect activate`
uses the legacy two-step sequence. `effect set` is a compatible
synonym for select. Use visible slugs from the catalog or their
canonical schema names; hidden schema values cannot be written.
For a custom endpoint, place `--socket PATH` before the command.
See [TECH](TECH.md) for the complete socket API.

### Experimental lighting and countdown

The CLI retains `lighting temperature` and
`lighting temperature=5000`; composition support has not
established useful visible temperature control. Light xyL is
not present in the tested primary composition.

Countdown uses vendor DP 101:

```sh
spherospherectl --device ARI countdown get
spherospherectl --device ARI countdown set 60
spherospherectl --device ARI countdown cancel
```

Set accepts 0–43200 seconds; cancel sets zero. Firmware analysis
shows expiry toggles power, so do not treat it as unconditional
shutdown. Only zero has live write/readback validation; positive
durations and expiry remain untested on hardware. Writes are
not automatically retried.

## Connection and error behavior

**Daemon offline** means the GUI cannot reach its socket.
Check the service and Settings' endpoint. The app reconnects
automatically and offers **Retry now**. **Mesh offline** means
the daemon is reachable but its Bluetooth transport is not
ready. **Daemon busy** means a request timed out and may still
finish; reconnecting never repeats a lighting write.

Device API errors do not mark the daemon offline. Errors remain
visible until dismissed; dismissing a device error does not
hide a daemon outage. Controls pause during commands and while
the radio connection is unavailable. The app refreshes after
connection-generation changes or when it regains focus.

A partial activation error identifies completed and failed
steps. Use **Refresh State** before deciding to retry. Do not
assume a timeout proves that a write had no effect.

## Diagnostics and recovery

Start with local observations:

```sh
systemctl --user status spherosphere.service
journalctl --user -u spherosphere.service -n 50
spherospherectl health
spherospherectl devices
spherospherectl --device ARI control read
```

`health` sends no Mesh command. Attachment means the host is
ready, not that every lamp is reachable. A targeted Get checks
that particular lamp independently. For Mesh attachment errors,
inspect `last_failure` and the system Mesh service journal too.
Check the light's charge, power and range, and keep phone
Bluetooth disabled while diagnosing competing connections.
Do not delete state to troubleshoot a connection.

For command tracing, set `verbose = true` and restart, or stop
the user service and run `spherosphered --verbose`. JSON lines
show accepted operations, elapsed times, decoded replies and
errors. Daemon tracing redacts keys, raw vendor bytes and invalid
parameters. Direct harness frame dumps and radio traces have
different exposure: review those artifacts before sharing.

The socket has mode `0600`. The daemon refuses to replace a
regular file at its path. It removes a stale socket only after
confirming that no service is listening. State-lock failures
usually mean another owner is still running; stop that process
rather than bypassing the lock.

### Verification and evidence

An acknowledged write confirms its expected response, not an
independent readback or visible appearance. An empty vendor
acknowledgment reports no value. Read results can still omit
requested datapoints; inspect `missing_dp_ids` and verification
fields. Cached display metadata does not count as fresh readback.
Current API operations never claim human visual verification.

Dedicated generic I/O control returned acknowledged Sets and
independent OnOff Gets from two lamps over the advertising
bearer. A trace showed no GATT connection creation or ACL data.
Host transmit and relay intervals of 100 ms resolved rejected
10 ms advertising parameters; host retransmissions are disabled
and host secure beacons are off, with Relay enabled. Remaining
random-address rotation errors did not prevent that run.

Concurrent USB discovery and eight paired Mesh reads passed.
These results establish endpoint access and concurrent scanning.
They do not prove physical multi-hop forwarding, maximum range,
audio coexistence or long-duration reliability. Shared-controller
MGMT transmit failures and recovery stalls remain unresolved;
dedicated generic I/O success does not repair that backend.
Audio plus multi-hop operation remains unproven.

The checkout's read-only split diagnostic performs fresh USB
observation and targeted Mesh reads:

```sh
./bin/spherosphere-bt check-split UNIQUE-NAME --seconds 30
```

It does not change lamp output or make the controller setup
persistent. Private results and radio traces belong in ignored
`state/captures/`; they may contain nearby device metadata.
Offline operational checks do not contact hardware:

```sh
./.venv/bin/python -m pytest -q tests/test_operational.py
```

### Physical recovery and ownership changes

For Sphere C, three rapid button presses while on return the
light to green-flashing unprovisioned mode without the former
controller. This is the preferred physical recovery path when
transferring ownership. In the tested Olight app, **Remove
Device**, then **Clear**, resets membership; **Restore Factory
Colors** is not that network-reset operation. Its captured
DP106 handler resets broader lighting defaults and button
presets, including powering off; no Mesh-ownership reset was
established in that branch. See [defaults behavior](FIRMWARE.md#application-routing-and-dp-writes).

An authenticated direct Config Node Reset is also available
through `spherosphere-provision --reset-node`. Stop the daemon
first and use the correct saved state and target UUID. This
changes the light's network membership; it is not a routine
connection diagnostic. Recovery keys remain locally retained.

## Self-provisioning and direct diagnostics

The daemon/GUI enrollment flow is preferred for normal use.
The direct harness supports PB-GATT provisioning and inspection
for controlled investigations. Stop the application daemon
before opening its state. Do not create a second copied state
for the same controller identity.

Initialize or validate state without Bluetooth:

```sh
spherosphere-provision --init-only
```

Scan advertisements and use the full observed Device UUID;
never derive one lamp's UUID from another lamp's suffix:

```sh
./.venv/bin/python scripts/scan_mesh.py
spherosphere-provision --device-uuid DEVICE_UUID --no-toggle
```

That command provisions and completes standard configuration
without a light toggle. To intentionally stop after provisioning,
use `--provision-only`; a partial handshake alone does not prove
durable usable membership. Full setup adds the AppKey, reads
composition, performs all 11 allowlisted model bindings and
sets Network Transmit. Which individual late operation commits
membership has not been isolated.

Read an existing selected node without provisioning again:

```sh
spherosphere-provision --device-uuid DEVICE_UUID --control read
```

`--toggle-only --no-toggle` is a reuse/read operation, not a
replacement for full configuration. Without `--no-toggle`, the
ordinary harness workflow may toggle output. Review `--help`
and target selection before using it on a multi-light network.

Inspect a saved node's model inventory without adding an AppKey,
binding models, or changing its light:

```sh
spherosphere-provision --device-uuid DEVICE_UUID --probe-composition
```

This DeviceKey-authenticated read can diagnose a partially
configured node. A timeout is inconclusive because the reply
can require segmented transport. The command still advances
and persists the controller's sequence state. Stop the daemon
before using this direct diagnostic with its state file.

A retained Network ID and authenticated reads after disconnect
and button-off/wake established persistent membership. A
button-off cycle is not proof of complete power loss because
the internal battery remains connected.

The direct path supports static OOB with `--static-oob HEX`,
but not input/output OOB or PB-ADV. Its pinned dependency
assembles and acknowledges segmented unicast responses; missing
outbound-segment retransmission is incomplete. Direct-path IV
Index recovery and durable receive replay protection are not
implemented. Immediate duplicate suppression is not a complete
Replay Protection List. BlueZ owns these host responsibilities
when using its Mesh backend.

## Network ownership

Local enrollment creates independent keys and requires no
vendor-account key extraction. Resetting a Sphere into pairing
mode replaces its previous network ownership. Keep local Mesh
state private and protect backups as described above.
