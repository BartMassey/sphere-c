# Spherosphere

Spherosphere is a Linux desktop application for controlling
**Olight Sphere C** lights without the Olight app or cloud.
Enroll your lights into a local Bluetooth Mesh network, give
them names, and control them from one window.

<p>&emsp;&emsp;<img src="packaging/desktop/aurora-readme.animated.webp" alt="An Olight Sphere C playing the Aurora animation" width="240"></p>

The app provides:

- A selector for controlling multiple Spheres independently.
- Power, brightness, and solid-color controls.
- 25 built-in patterns and animations, with recorded previews.
- Readback of the current scene and work mode.
- Battery status, enrollment, and device naming.

Spherosphere controls the light's built-in effects. Uploading
custom scenes or animations is not supported. The tested device
is the USB-C Sphere C with firmware **0.5**; the earlier
magnetic-charge Sphere has not been validated.

## Install

You need Linux with BlueZ, a working Bluetooth Low Energy
adapter, Python **3.12 or newer**, Git, and a desktop session.
The instructions below use a systemd user service to keep the
daemon running. Python's `venv` module must be installed; on
Debian it is provided by `python3-venv`.

Download the source and install into a persistent virtual
environment:

```sh
git clone https://github.com/BartMassey/spherosphere.git
cd spherosphere
python3 -m venv ~/.local/share/spherosphere/venv
. ~/.local/share/spherosphere/venv/bin/activate
python -m pip install '.[gui]'
```

The installation includes the Qt desktop dependencies. It needs
network access to fetch dependencies, including a pinned Git
dependency. The installed application does not require the
source checkout afterward.

For a first installation, start the daemon in **proxy** mode:

```sh
spherosphere-service install --profile proxy --enable-now
spherosphere
```

Run these commands as your normal user, without `sudo`.
The daemon owns the Bluetooth connection; the GUI talks to it
locally. Do not also start a second daemon in a terminal.

To launch the app from a later terminal:

```sh
. ~/.local/share/spherosphere/venv/bin/activate
spherosphere
```

If you already have an installation, keep its settings and Mesh
state. See the [migration and service guide](docs/GUIDE.md#moving-an-older-installation)
before changing its state location or service profile.

Proxy mode shares your normal Bluetooth adapter; alternatively,
the daemon can be a Mesh member using a dedicated controller,
with an optional second controller for ordinary Bluetooth.
As a Mesh member it can relay packets between lights, letting a
centrally placed computer help bridge the Mesh, but the setup
is more involved and reserves one controller for Mesh use.
See [daemon profiles](docs/GUIDE.md#daemon-profiles) for the
tradeoffs and setup.

## Enroll a Sphere

Charge the Sphere enough to turn on before starting. Enrollment
currently accepts Sphere C firmware **0.5**. Spherosphere does
not install firmware updates. If your Sphere has older firmware,
complete its update in Olight before starting this sequence.

1. **Disable Bluetooth on any phone with Olight installed.**
   Leave it disabled during enrollment so Olight cannot connect
   to or enroll the Sphere first.
2. **Turn the Sphere on using its button.**
3. **Press the Sphere's button three times rapidly.** Confirm
   that it is blinking green: this is its pairing mode.
4. **Start enrollment in Spherosphere.** Click **+** in the main
   window, then **Enroll nearby Spheres**.
5. Wait for the Sphere to show **Enrolled**. Discovery and setup
   run automatically; a unique default name is assigned.
6. Stop enrollment, rename the Sphere if desired, and select it
   in the main window. Pressing Enter accepts a new name.

You do not need to pair the Sphere in your desktop's Bluetooth
settings. Spherosphere performs Bluetooth Mesh enrollment itself.
Putting a previously enrolled Sphere back into pairing mode is
the physical action that permits it to join another network.

The discovery list shows devices as they are found, then shows
their setup progress. If pairing mode expires, put the Sphere
back into green-blinking mode and start enrollment again.
Closing **Manage Spheres** stops further enrollment attempts;
already enrolled devices and their names are retained.

## Use the app

Choose a Sphere by name in the main window. The controls apply
to that Sphere, and selecting another light reads its current
state and selects the corresponding source in the UI.

- **Power:** use the global power button. The preview is black
  when the light is off.
- **Light:** adjust brightness or select a solid color with the
  color controls.
- **Effects:** browse **Patterns** and **Animations**, search by
  name, and select a tile to apply a built-in effect. The small
  preview beside Power follows the selected light's scene and
  brightness; gallery previews stay at a fixed display level.
- **Manage Spheres (+):** enroll more lights, rename them, or
  forget them locally. **Forget** does not reset the physical
  Sphere or revoke its Mesh membership.
- **Settings:** inspect connection information, battery status,
  and diagnostics.

The app distinguishes current readback from a **Last known**
fallback when a fresh status is unavailable. Recorded previews
illustrate the effect; they are not a live camera view.

If the app says **Daemon offline**, check the service:

```sh
systemctl --user status spherosphere.service
journalctl --user -u spherosphere.service -n 50
```

If the daemon is running but a light is unavailable, check that
the Sphere is charged and on, and that phone Bluetooth is off.
Do not delete the state file to troubleshoot a connection.

## Settings and local data

The app and daemon use shared XDG paths, independent of the
source directory:

| Purpose | Default location |
|---|---|
| Configuration | `~/.config/spherosphere/daemon.toml` |
| Mesh keys and device names | `~/.local/state/spherosphere/state.json` |
| Local control socket | `$XDG_RUNTIME_DIR/spherosphere/control.sock` |

The standard XDG environment variables can change these roots.
The state file contains the identity needed to control enrolled
lights: retain it, keep it private, and never restore an older
copy over an active network. The [technical report](docs/TECH.md)
covers path overrides, service profiles, and state migration.

For application-menu integration and more detailed controls,
see the [desktop guide](docs/GUIDE.md#desktop-integration).
For the investigation behind the software, read the
[project report](docs/REPORT.md).

## Future Work

The next priorities, in order, are:

1. **Music mode:** understand the device's audio-reactive mode
   and implement useful music controls in Spherosphere.
2. **Secure remote access:** provide a REST API for the daemon,
   allowing an Android app or other client to control lights
   securely over the Internet, with authentication,
   authorization, and encrypted connections.
3. **Custom scenes and animations:** modify the firmware to
   support uploading and playing user-created content.

These are planned capabilities, not features of the current
release. See the [technical roadmap](docs/TECH.md#future-work)
for the implementation questions and existing evidence.

## License

Spherosphere is MIT-licensed; see [LICENSE.txt](LICENSE.txt).
The recorded light previews are CC0; their
[provenance](packaging/desktop/ASSETS.md) is included.
