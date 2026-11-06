"""Process-level smoke checks for the optional desktop application."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

from spherosphere.gui import app as gui_app


def default_socket_path():
    return gui_app.default_socket_path()


def test_application_icon_path_finds_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    icon = checkout / "packaging" / "desktop" / "spherosphere.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text("<svg/>")
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", checkout)
    monkeypatch.setattr(gui_app.sys, "prefix", str(tmp_path / "prefix"))
    assert gui_app.application_icon_path() == icon


def test_default_socket_path_prefers_override(monkeypatch, tmp_path):
    socket = tmp_path / "sphere.sock"
    monkeypatch.setenv("SPHEROSPHERE_SOCKET", str(socket))
    assert default_socket_path() == socket


def test_default_socket_path_waits_for_installed_service(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHEROSPHERE_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", tmp_path / "installed")
    assert default_socket_path() == (
        tmp_path / "run" / "spherosphere" / "control.sock"
    )


def test_default_socket_path_ignores_development_checkout(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHEROSPHERE_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", tmp_path / "installed")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "sphere_state.json").write_text("{}")
    assert default_socket_path() == tmp_path / "run/spherosphere/control.sock"


def test_default_socket_path_ignores_editable_checkout(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHEROSPHERE_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    launch_dir = tmp_path / "elsewhere"
    checkout = tmp_path / "checkout"
    launch_dir.mkdir()
    (checkout / "state").mkdir(parents=True)
    (checkout / "state" / "sphere_state.json").write_text("{}")
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", checkout)
    assert default_socket_path() == tmp_path / "run/spherosphere/control.sock"


def test_qml_application_loads_offscreen(tmp_path):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QUICK_BACKEND": "software",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "spherosphere.gui.app",
            "--socket",
            str(tmp_path / "missing.sock"),
            "--test-quit-after-load",
        ],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_qml_brightness_scale_is_logarithmic():
    script = """
from pathlib import Path
import tempfile
from PySide6.QtCore import QObject, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlApplicationEngine
from spherosphere.gui.backend import SphereBackend

app = QGuiApplication([])
missing_socket = tempfile.TemporaryDirectory(prefix="sphere-gui-qml-")
backend = SphereBackend(Path(missing_socket.name) / "sphere.sock", start=False)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("sphere", backend)
engine.load(str(Path("src/spherosphere/gui/qml/Main.qml").resolve()))
root = engine.rootObjects()[0]
feedback = root.findChild(QObject, "feedbackCard")
state_label = root.findChild(QObject, "sphereStateLabel")
assert state_label is not None
backend._put(connected=True, power=True,
             effects=[{"slug": "aurora", **backend._preset_catalog["aurora"]}],
             effect={"slug": "aurora", "label": "Aurora"}, effectActive=True)
app.processEvents()
assert root.property("currentPage") == 0
assert state_label.property("text") == "Aurora"
main_preview = root.findChild(QObject, "mainPreview")
main_recording = root.findChild(QObject, "mainRecordedPreview")
assert main_preview.property("visible")
assert main_preview.property("width") <= 60
assert main_recording.property("visible")
assert main_recording.property("animate")
assert main_recording.property("animationUrl").toString().endswith("aurora.animated.webp")
QTest.qWait(350)
main_animation = main_recording.findChild(QObject, "recordedAnimation")
assert main_animation is not None
assert main_animation.property("frameCount") > 1
frame = main_animation.property("currentFrame")
QTest.qWait(160)
assert main_animation.property("currentFrame") != frame
backend._put(effectActive=False, previewKind="light")
app.processEvents()
assert state_label.property("text") == "Light on"
assert not main_recording.property("visible")
backend._put(effectActive=True, effect={"slug": "blue-sky", "label": "Blue sky"},
             effects=[{"slug": "blue-sky", **backend._preset_catalog["blue-sky"]}])
app.processEvents()
assert main_recording.property("visible")
assert main_recording.property("stillUrl").toString().endswith("blue-sky.webp")
assert main_recording.property("animationUrl").toString() == ""
backend._put(power=False)
app.processEvents()
assert not main_recording.property("visible")
off_preview = root.findChild(QObject, "mainPreviewOff")
assert off_preview.property("visible")
assert off_preview.property("color").name() == "#000000"
assert not root.findChild(QObject, "mainPreviewIllustration").property("visible")
backend._put(connected=False, power=None)
shared_controls = root.findChild(QObject, "sharedLightControls")
pages = root.findChild(QObject, "pages")
power_button = root.findChild(QObject, "powerButton")
brightness_slider = root.findChild(QObject, "brightnessSlider")
assert shared_controls is not None
assert pages is not None
assert power_button is not None
assert brightness_slider is not None
assert shared_controls.property("visible")
assert root.findChildren(QObject, "brightnessSlider") == [brightness_slider]
root.setProperty("currentPage", 1)
app.processEvents()
assert shared_controls.property("visible")
assert power_button.property("visible")
assert brightness_slider.property("visible")
root.setProperty("height", 520)
root.setProperty("width", 320)
app.processEvents()
assert brightness_slider.property("width") > 0
assert brightness_slider.property("height") > 0
assert pages.property("height") > 0
assert shared_controls.property("y") + shared_controls.property("height") <= pages.property("y")
root.setProperty("currentPage", 2)
app.processEvents()
assert shared_controls.property("visible")
assert power_button.property("visible")
assert main_preview.property("visible")
assert not brightness_slider.property("visible")
root.setProperty("currentPage", 0)
app.processEvents()
assert shared_controls.property("visible")
assert feedback.property("visible")
backend._put(connected=True)
assert not feedback.property("visible")
backend._put(pending=True, pendingAction="Updating Sphere")
assert feedback.property("visible")
backend._put(pending=False, errorMessage="A device error")
assert feedback.property("visible")
backend.clearError()
assert not feedback.property("visible")
backend._put(connected=False)
assert feedback.property("visible")
assert root.brightnessLightness(0) == 1966
assert root.brightnessLightness(100) == 65535
assert 1966 < root.brightnessLightness(50) < 65535 / 2
assert root.brightnessPosition(0) == 0
assert root.brightnessPosition(1966) == 0
assert abs(root.previewBrightness(0) - 0.62) < 1e-9
assert abs(root.previewBrightness(50) - 0.81) < 1e-9
assert abs(root.previewBrightness(100) - 1.0) < 1e-9
for lightness in (1966, 12000, 32768, 65535):
    assert abs(root.brightnessLightness(root.brightnessPosition(lightness)) - lightness) <= 1
calls = []
backend._enqueue = lambda request: calls.append(request)
backend._put(
    connected=True,
    capabilities={"hsl": True, "effects": True},
    hsl={"lightness": 32768, "hue": 0, "saturation": 65535},
)
button = root.findChild(QObject, "applyColorButton")
assert button.property("enabled")
assert QMetaObject.invokeMethod(button, "click")
assert calls[-1].method == "effect.activate"
assert calls[-1].params == {"name": "color-wheel"}
backend._put(pending=False)
calls.clear()
root.setProperty("currentPage", 1)
root.selectEffect("color-wheel")
assert root.property("currentPage") == 0
assert calls[-1].method == "effect.activate"
assert calls[-1].params == {"name": "color-wheel"}
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_effect_tiles_form_responsive_filtered_grid():
    script = """
from pathlib import Path
import tempfile
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from spherosphere.gui.backend import SphereBackend

app = QGuiApplication([])
missing_socket = tempfile.TemporaryDirectory(prefix="sphere-gui-grid-")
backend = SphereBackend(Path(missing_socket.name) / "sphere.sock", start=False)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("sphere", backend)
engine.load(str(Path("src/spherosphere/gui/qml/Main.qml").resolve()))
root = engine.rootObjects()[0]
grid = root.findChild(QQuickItem, "effectsGrid")
repeater = root.findChild(QObject, "effectRepeater")
search = root.findChild(QObject, "effectSearch")
assert grid is not None and repeater is not None and search is not None
effects = [
    {"slug": "effect-" + str(index), "label": "Effect " + str(index), "visible": True,
     "category": "pattern" if index < 20 else "animation",
     "animationUrl": "file:///missing-preview.gif" if index == 20 else ""}
    for index in range(25)
]
effects.append({"slug": "hidden", "label": "Hidden", "visible": False})
backend._put(effects=effects, effect=effects[2], effectActive=True)
root.setProperty("currentPage", 1)
app.processEvents()
backend._put(effectActive=True)
app.processEvents()
assert repeater.property("count") == 20
tiles = [item for item in grid.childItems() if item.objectName() == "effectTile"]
assert len(tiles) == 20
by_slug = {tile.property("effectSlug"): tile for tile in tiles}
assert grid.property("columns") == 3
assert all(tile.property("height") > 60 for tile in tiles)
assert len({round(by_slug["effect-" + str(index)].property("x")) for index in range(3)}) == 3
assert by_slug["effect-2"].property("selected")
backend._put(pending=True, pendingAction="Changing brightness")
app.processEvents()
unchanged_tiles = [item for item in grid.childItems() if item.objectName() == "effectTile"]
assert all(any(item is tile for item in unchanged_tiles) for tile in tiles)
backend._put(pending=False, pendingAction="")
brightness_slider = root.findChild(QObject, "brightnessSlider")
dimming = by_slug["effect-2"].findChild(QObject, "effectPreviewRendering")
brightness_slider.setProperty("previewValue", 100)
app.processEvents()
assert abs(dimming.property("gain") - 1.8) < 1e-6
brightness_slider.setProperty("previewValue", 0)
app.processEvents()
assert abs(dimming.property("gain") - 1.8) < 1e-6
brightness_slider.setProperty("previewValue", 50)
app.processEvents()
assert abs(dimming.property("gain") - 1.8) < 1e-6
brightness_slider.setProperty("previewValue", 25)
app.processEvents()
assert abs(dimming.property("gain") - 1.8) < 1e-6

assert grid.property("height") > root.findChild(QObject, "effectsScroll").property("height")
root.setProperty("width", 320)
app.processEvents()
assert grid.property("columns") == 2
assert len({round(by_slug["effect-" + str(index)].property("x")) for index in range(2)}) == 2
assert by_slug["effect-2"].property("y") > by_slug["effect-0"].property("y")
search.setProperty("text", "Effect 1")
app.processEvents()
assert repeater.property("count") == 11
tiles = [item for item in grid.childItems() if item.objectName() == "effectTile"]
assert len(tiles) == 11
assert all("Effect 1" in tile.property("effectName") for tile in tiles)
backend._put(connected=True, capabilities={"effects": True}, pending=False)
calls = []
backend._enqueue = lambda request: calls.append(request)
target = next(tile for tile in tiles if tile.property("effectSlug") == "effect-1")
target.forceActiveFocus()
app.processEvents()
assert target.property("activeFocus")
QTest.keyClick(root, Qt.Key_Return)
assert calls[-1].method == "effect.activate"
assert calls[-1].params == {"name": "effect-1"}
search.setProperty("text", "")
root.setProperty("effectCategory", "animation")
app.processEvents()
assert repeater.property("count") == 5
animations = [item for item in grid.childItems() if item.objectName() == "effectTile"]
assert all(tile.property("effectSlug") in {"effect-" + str(i) for i in range(20, 25)} for tile in animations)
assert all(not tile.findChild(QObject, "effectAnimationLoader").property("active") for tile in animations)
assert all(tile.findChild(QObject, "previewFallback").property("visible") for tile in animations)
animated = next(tile for tile in animations if tile.property("effectSlug") == "effect-20")
animated.forceActiveFocus()
app.processEvents()
loader = animated.findChild(QObject, "effectAnimationLoader")
assert loader.property("active")
animation_dimming = animated.findChild(QObject, "effectPreviewRendering")
brightness_slider.setProperty("previewValue", 0)
app.processEvents()
assert abs(animation_dimming.property("gain") - 1.8) < 1e-6
brightness_slider.setProperty("previewValue", 100)
app.processEvents()
assert abs(animation_dimming.property("gain") - 1.8) < 1e-6
assert len(calls) == 1
root.setProperty("currentPage", 0)
app.processEvents()
assert not loader.property("active")
root.setProperty("currentPage", 1)
search.setProperty("text", "Effect 21")
app.processEvents()
assert repeater.property("count") == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_management_dialog_has_named_inventory_and_automatic_enrollment():
    script = '''
from pathlib import Path
import tempfile
from PySide6.QtCore import QObject, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from spherosphere.gui.backend import SphereBackend

app = QGuiApplication([])
with tempfile.TemporaryDirectory() as directory:
    backend = SphereBackend(Path(directory) / 'missing.sock', start=False)
    backend._put(devices=[{'device': '0x012a', 'name': 'ARI', 'configured': True},
                          {'device': '0x0131', 'name': 'BEN', 'configured': True}],
                 device='0x012a', serviceAvailable=True)
    backend._enqueue = lambda request: None
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('sphere', backend)
    engine.load(str(Path('src/spherosphere/gui/qml/Main.qml').resolve()))
    root = engine.rootObjects()[0]
    app.processEvents()
    selector = root.findChild(QObject, 'deviceSelector')
    assert selector.property('count') == 2
    assert selector.property('currentText') == 'ARI'
    button = root.findChild(QObject, 'manageSpheresButton')
    assert QMetaObject.invokeMethod(button, 'click')
    app.processEvents()
    dialog = root.findChild(QObject, 'manageSpheresDialog')
    assert dialog.property('visible')
    assert dialog.nameAvailable('CYR', '')
    assert not dialog.nameAvailable(' ari ', '')
    assert dialog.nameAvailable('BEN', '0x0131')
    def find_visual(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = find_visual(child, name)
            if found is not None:
                return found
        return None
    toggle = find_visual(dialog.property('contentItem'), 'enrollmentToggle')
    assert toggle.property('text') == 'Enroll nearby Spheres'
    backend._accept_enrollment({'active': True, 'phase': 'checking',
                                'remaining_seconds': 100, 'candidates': []})
    app.processEvents()
    assert 'Checking compatibility' in find_visual(dialog.property('contentItem'), 'enrollmentProgress').property('text')
    candidates = [{'candidate': 'uuid', 'name': 'Sphere123C04', 'state': 'provisioning'}]
    backend._accept_enrollment({'active': True, 'phase': 'provisioning',
                                'remaining_seconds': 99, 'candidates': candidates})
    app.processEvents()
    assert find_visual(dialog.property('contentItem'), 'candidateName').property('text') == 'Sphere123C04'
    assert 'Provisioning' in find_visual(dialog.property('contentItem'), 'candidateStatus').property('text')
    assert find_visual(dialog.property('contentItem'), 'candidateNameField') is None
    assert find_visual(dialog.property('contentItem'), 'candidateAction') is None
    assert toggle.property('text') == 'Stop enrollment'
    failed = [{'candidate': 'uuid', 'name': 'Sphere123C04', 'state': 'failed'}]
    backend._accept_enrollment({'active': True, 'candidates': failed, 'errors': ['failed']})
    app.processEvents()
    assert 'Enrollment continues for other Spheres' in find_visual(dialog.property('contentItem'), 'candidateStatus').property('text')
    backend._accept_enrollment({'active': False, 'candidates': failed})
    app.processEvents()
    assert 'Start discovery to retry' in find_visual(dialog.property('contentItem'), 'candidateStatus').property('text')
    recovering = [{'candidate': 'uuid', 'name': 'Sphere123C04', 'state': 'recovering'}]
    backend._accept_enrollment({'active': True, 'candidates': recovering, 'errors': ['failed']})
    app.processEvents()
    assert 'Finishing setup automatically' in find_visual(dialog.property('contentItem'), 'candidateStatus').property('text')
    backend._accept_enrollment({'active': False, 'candidates': recovering})
    app.processEvents()
    assert 'Start discovery to resume' in find_visual(dialog.property('contentItem'), 'candidateStatus').property('text')
    backend._accept_enrollment({'active': True, 'candidates': candidates})
    app.processEvents()
    assert not selector.property('enabled')
    backend._put(enrollment={'active': False}, serviceAvailable=False,
                 serviceState='unavailable', serviceError='Connection refused',
                 connected=False)
    app.processEvents()
    title = root.findChild(QObject, 'connectionProblemTitle')
    assert title.property('text') == 'Cannot reach the Sphere daemon'
    assert root.findChild(QObject, 'connectionStatusLabel').property('text') == 'Daemon offline'
    assert root.findChild(QObject, 'daemonRecoveryHint').property('visible')
    retry = root.findChild(QObject, 'retryDaemonButton')
    assert retry.property('visible')
    calls = []
    backend._enqueue = calls.append
    backend._status_due = False
    assert QMetaObject.invokeMethod(retry, 'click')
    assert calls[-1].method == 'device.list'
    backend._put(serviceAvailable=True, serviceState='ready', serviceError='',
                 connected=False)
    app.processEvents()
    assert title.property('text') == 'The daemon is running, but Bluetooth Mesh is unavailable'
    assert not retry.property('visible')
    backend.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', script], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
