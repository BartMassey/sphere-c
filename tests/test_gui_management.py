"""Offscreen checks for the management dialog."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")


def test_management_lists_and_rename_enter():
    script = r'''
from pathlib import Path
from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, QMetaObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle

class FakeBackend(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.inventory = [{"device": str(i), "name": f"Sphere {i}"} for i in range(3)]
        self.candidates = []
        self.active = False
        self.pending = False
        self.available = True
        self.calls = []
    devices = Property("QVariant", lambda s: s.inventory, notify=changed)
    enrollmentCandidates = Property("QVariant", lambda s: s.candidates, notify=changed)
    enrollment = Property("QVariant", lambda s: {"active": s.active, "candidates": s.candidates}, notify=changed)
    serviceAvailable = Property(bool, lambda s: s.available, notify=changed)
    managementPending = Property(bool, lambda s: s.pending, notify=changed)
    managementError = Property(str, lambda s: "", notify=changed)
    @Slot()
    def refreshDevices(self): pass
    @Slot()
    def startEnrollment(self): pass
    @Slot()
    def stopEnrollment(self): pass
    @Slot(str, str)
    def renameDevice(self, device, name): self.calls.append((device, name))
    @Slot(str)
    def forgetDevice(self, device): pass

QQuickStyle.setStyle("Material")
app = QGuiApplication([])
backend = FakeBackend()
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("fakeBackend", backend)
qml_dir = Path("src/spherosphere/gui/qml").resolve().as_uri()
qml = f''' + "'''" + r'''
import QtQuick
import QtQuick.Controls
import "{qml_dir}" as Components
ApplicationWindow {{
    width: 320; height: 520; visible: true
    Components.ManageSpheresDialog {{
        backend: fakeBackend
        Component.onCompleted: open()
    }}
}}
''' + "'''" + r'''
engine.loadData(qml.encode(), QUrl("file:///tmp/management-test.qml"))
assert engine.rootObjects()
root = engine.rootObjects()[0]
QTest.qWait(100)
manage = root.findChild(QObject, "manageSpheresDialog")
assert manage and manage.property("visible")
items = root.findChild(QObject, "spheresList")
assert items.property("count") == 3
rows = sorted((row for row in items.property('contentItem').childItems()
               if row.objectName() == 'enrolledSphereRow'), key=lambda row: row.y())
assert len(rows) == 3
assert abs(items.property('height') - sum(row.height() for row in rows)) < 1
for row in rows:
    name = row.findChild(QObject, 'enrolledSphereName')
    assert name is not None
    assert 5 <= row.height() - name.property('implicitHeight') <= 7
    assert abs(name.height() - row.height()) < 1
assert all(0 <= following.y() - (row.y() + row.height()) <= 6
           for row, following in zip(rows, rows[1:]))
backend.inventory = [{"device": str(i), "name": f"Sphere {i}"} for i in range(30)]
backend.candidates = [{"name": f"Found {i}", "state": "found"} for i in range(30)]
backend.changed.emit()
QTest.qWait(100)
assert items.property("count") == 30
assert items.property("contentHeight") > items.property("height")
found = root.findChild(QQuickItem, "discoveredSpheresList")
assert found.property("count") == 30
assert found.property("contentHeight") > found.property("height")
def find_visual(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        result = find_visual(child, name)
        if result:
            return result
    return None
assert find_visual(found, "candidateStatus").property("text") == "Found"
rename = root.findChild(QObject, "renameDialog")
field = root.findChild(QObject, "renameNameField")
save = root.findChild(QObject, "renameSaveButton")
rename.setProperty("device", "0")
def open_rename(name):
    field.setProperty("text", name)
    QMetaObject.invokeMethod(rename, "open")
    QTest.qWait(200)  # Material's opening transition must finish before typing.
def enter():
    QTest.keyClick(root, Qt.Key.Key_Return)
    QTest.qWait(30)
open_rename("  ")
assert not save.property("enabled")
enter()
assert not backend.calls and rename.property("visible")
field.setProperty("text", "sphere 1")
assert not save.property("enabled")
enter()
assert not backend.calls and rename.property("visible")
field.setProperty("text", " New name ")
backend.active = True
backend.changed.emit()
assert not save.property("enabled")
enter()
assert not backend.calls
backend.active = False
backend.changed.emit()
assert save.property("enabled")
enter()
enter()
assert backend.calls == [("0", "New name")]
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
             "QT_QUICK_BACKEND": "software"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
