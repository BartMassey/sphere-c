"""Process-level smoke checks for the optional desktop application."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

from sphere_mesh.gui import app as gui_app


def default_socket_path():
    return gui_app.default_socket_path()


def test_application_icon_path_finds_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    icon = checkout / "packaging" / "desktop" / "sphere-mesh-gui.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text("<svg/>")
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", checkout)
    monkeypatch.setattr(gui_app.sys, "prefix", str(tmp_path / "prefix"))
    assert gui_app.application_icon_path() == icon


def test_default_socket_path_prefers_override(monkeypatch, tmp_path):
    socket = tmp_path / "sphere.sock"
    monkeypatch.setenv("SPHERE_MESH_SOCKET", str(socket))
    assert default_socket_path() == socket


def test_default_socket_path_waits_for_installed_service(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHERE_MESH_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", tmp_path / "installed")
    assert default_socket_path() == (
        tmp_path / "run" / "sphere-mesh" / "control.sock"
    )


def test_default_socket_path_recognizes_development_checkout(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHERE_MESH_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", tmp_path / "installed")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "sphere_state.json").write_text("{}")
    assert default_socket_path() == tmp_path / "state" / "sphere.sock"


def test_default_socket_path_finds_editable_checkout(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SPHERE_MESH_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    launch_dir = tmp_path / "elsewhere"
    checkout = tmp_path / "checkout"
    launch_dir.mkdir()
    (checkout / "state").mkdir(parents=True)
    (checkout / "state" / "sphere_state.json").write_text("{}")
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(gui_app, "SOURCE_CHECKOUT_ROOT", checkout)
    assert default_socket_path() == checkout / "state" / "sphere.sock"


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
            "sphere_mesh.gui.app",
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
from PySide6.QtQml import QQmlApplicationEngine
from sphere_mesh.gui.backend import SphereBackend

app = QGuiApplication([])
missing_socket = tempfile.TemporaryDirectory(prefix="sphere-gui-qml-")
backend = SphereBackend(Path(missing_socket.name) / "sphere.sock", start=False)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("sphere", backend)
engine.load(str(Path("src/sphere_mesh/gui/qml/Main.qml").resolve()))
root = engine.rootObjects()[0]
feedback = root.findChild(QObject, "feedbackCard")
state_label = root.findChild(QObject, "sphereStateLabel")
assert state_label is not None
backend._put(connected=True, power=True, effect={"label": "Aurora"}, effectActive=True)
app.processEvents()
assert root.property("currentPage") == 0
assert state_label.property("text") == "Aurora"
backend._put(effectActive=False)
app.processEvents()
assert state_label.property("text") == "Light on"
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
assert not shared_controls.property("visible")
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
from sphere_mesh.gui.backend import SphereBackend

app = QGuiApplication([])
missing_socket = tempfile.TemporaryDirectory(prefix="sphere-gui-grid-")
backend = SphereBackend(Path(missing_socket.name) / "sphere.sock", start=False)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("sphere", backend)
engine.load(str(Path("src/sphere_mesh/gui/qml/Main.qml").resolve()))
root = engine.rootObjects()[0]
grid = root.findChild(QQuickItem, "effectsGrid")
repeater = root.findChild(QObject, "effectRepeater")
search = root.findChild(QObject, "effectSearch")
assert grid is not None and repeater is not None and search is not None
effects = [
    {"slug": "effect-" + str(index), "label": "Effect " + str(index), "visible": True}
    for index in range(25)
]
effects.append({"slug": "hidden", "label": "Hidden", "visible": False})
backend._put(effects=effects, effect=effects[2], effectActive=True)
root.setProperty("currentPage", 1)
app.processEvents()
backend._put(effectActive=True)
app.processEvents()
assert repeater.property("count") == 25
tiles = [item for item in grid.childItems() if item.objectName() == "effectTile"]
assert len(tiles) == 25
by_slug = {tile.property("effectSlug"): tile for tile in tiles}
assert grid.property("columns") == 3
assert all(tile.property("height") == 60 for tile in tiles)
assert len({round(by_slug["effect-" + str(index)].property("x")) for index in range(3)}) == 3
assert by_slug["effect-2"].property("selected")
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
