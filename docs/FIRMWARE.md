# Sphere C firmware findings

This is an original summary of observed behavior and analysis
conclusions for one Sphere C build. It contains no firmware
bytes, decompiled code, extracted lookup tables, or acquisition
procedures. Protocol facts used by Spherosphere are documented
in [PROTOCOL](PROTOCOL.md).

## Processor identity

The analyzed code belongs to Telink's proprietary 32-bit TC32
instruction-set family. TC32 is not ARM. The exact fitted MCU
and physical flash capacity remain unconfirmed.

## Application routing and DP writes

- DP 7 and DP 101 control the same countdown. Expiry toggles
  power; it is not an unconditional shutdown timer.
- DP 8 accepts compact music updates in the appropriate mode.
  Spherosphere does not yet implement music streaming.
- DP 102 selects built-in presets and can update the active
  physical-button preset slot. It also powers the light on.
- DP 103 reports battery state. DP 104 affects sleep policy.
  DP 106 restores broad defaults, including button presets.
- The generic DP 6 scene property has no action in the examined
  ordinary dispatcher. That does not prove absence from every
  possible firmware version or interface.

## Work mode and reported state

Work mode, rendering effect, and physical-button preset slot
are separate state. Typed DP 2 writes do not change mode in the
examined ordinary dispatcher; a separate vendor command does.
Read mode with the current preset to interpret the GUI state.
See [scene readback](PROTOCOL.md) for the implemented messages
and their validation limits.

## Internal pixels and virtual coordinates

The product manual specifies 41 colored LEDs and 67 white LEDs.
Analysis supports 41 logical color pixels. Virtual coordinates
do not establish physical rings, orientation, or a remotely
addressable LED map. The earlier 64-RGB recollection was not
confirmed. Extracted coordinate data is not distributed.

## Upload-path conclusion and remaining questions

No validated command for arbitrary pixel frames, custom scenes,
or animation programs has been found. A generic SDK scene
format does not establish a working Sphere C upload feature.
Firmware updating is a separate mechanism and is not provided
by Spherosphere.

Adding custom-content support may require firmware changes.
Boot acceptance, integrity requirements, recovery and safe
code-growth space remain unresolved. No modified image has
been flashed. Music support and a secure remote API precede
this work in the [roadmap](TECH.md#future-work).
