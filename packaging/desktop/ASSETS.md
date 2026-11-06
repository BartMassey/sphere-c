# Desktop asset provenance

The packaged desktop application contains no artwork, animation, font,
or other media copied from the Olight or Thingclips phone application.
The archived phone files under `old/` are research inputs only and are
excluded from both Git and Python packages.

The application uses two original interface illustrations:

- `spherosphere.svg` was created for this repository by Codex at
  Bart Massey's direction.
- The Sphere illustration in `src/spherosphere/gui/qml/SpherePreview.qml`
  is constructed from original QML geometry and gradients created for
  this repository by Codex at Bart Massey's direction.

Copyright © 2026 Bart Massey. Both works are distributed under the MIT
License in the repository's `LICENSE.txt`.

## Recorded preset previews

`src/spherosphere/gui/assets/presets/` contains 25 photographic
WebP stills and 19 animated WebP previews. They were derived
from Logitech C615 recordings of Bart Massey's Sphere C,
captured by Codex at his direction on 2026-10-31. Bart approved
CC0-1.0 for the footage and its derived previews that day.

`aurora-readme.animated.webp` is a brighter README derivative
of the same Aurora recording. It retains the 320x320 crop,
color correction, 15-fps timing and 10.4-second loop, with an
RGB gain of 1.8 applied before WebP encoding at quality 90.
The app's original preview asset is unchanged. This derivative
is also CC0-1.0.
These media use CC0, separately from the MIT-licensed code
and interface illustrations.

The directory includes its own `LICENSE.txt`, preset catalog,
and provenance manifest with recording SHA-256 hashes, source
timestamps, preset values and verification. The raw recordings
remain under ignored `old/effect-captures/` and are not packaged.
`scripts/build_preset_previews.py` reproduces the cropped stills
and short loops from the approved private capture index.

The second recording uses closer framing, manual focus 102,
0.3 ms exposure and white balance 6500 K. Previews use a
camera-specific empirical color correction from Color Wheel
references; its matrix and limitations are in the provenance.
The archived MJPEG masters retain the original camera colors.
Stills and full-color animations are 320x320; loops use 15 fps.

The animation previews load on hover, keyboard focus or selection,
and stop loading when the Effects page is inactive. They are
short excerpts of recorded device behavior, not generated
artwork or a software recreation of the firmware's animation.

All text is rendered with the user's Qt/system fonts. No font file is
bundled. The remaining interface consists of Qt Quick Controls and
original QML layout and styling covered by the same repository license.
