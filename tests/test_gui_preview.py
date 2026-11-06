"""Check preview/source synchronization without opening Bluetooth."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip('PySide6')


def test_selected_sphere_source_and_preview_brightness_are_synchronized():
    script = r'''
from pathlib import Path
import tempfile
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from sphere_mesh.gui.backend import SphereBackend

app = QGuiApplication([])
with tempfile.TemporaryDirectory() as directory:
    backend = SphereBackend(Path(directory) / 'missing.sock', start=False)
    calls = []
    backend._enqueue = calls.append
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('sphere', backend)
    engine.load(str(Path('src/sphere_mesh/gui/qml/Main.qml').resolve()))
    root = engine.rootObjects()[0]
    backend._accept('effects', {'effects': [
        {'slug': 'aurora', 'label': 'Aurora', 'visible': True},
        {'slug': 'red-flash', 'label': 'Red Flash', 'visible': True},
    ]})
    backend._select_device('0x0002')
    backend._put(connected=True, power=True)
    backend._accept('effect', {'effect': {'slug': 'aurora', 'label': 'Aurora'},
                               'work_mode': 1, 'work_mode_source': 'readback',
                               'effect_source': 'readback'})
    QTest.qWait(100)
    assert root.property('currentPage') == 1
    aurora = next(item for item in backend.effects if item['slug'] == 'aurora')
    assert root.property('effectCategory') == aurora['category']
    main = root.findChild(QObject, 'mainRecordedPreview')
    assert main.property('visible') and main.property('animate')
    assert main.property('animationUrl').toString().endswith('aurora.animated.webp')
    assert not root.property('previewBrightnessKnown')
    assert abs(main.property('gain') - 1.8) < 1e-6
    backend._accept('hsl', {'state': {'lightness': 32768, 'hue': 0, 'saturation': 65535}})
    QTest.qWait(30)
    assert root.property('previewBrightnessKnown')
    assert abs(main.property('gain') - 3.4) < 1e-6
    grid = root.findChild(QQuickItem, 'effectsGrid')
    tile = next(item for item in grid.childItems()
                if item.objectName() == 'effectTile' and item.property('effectSlug') == 'aurora')
    assert tile.property('selected')
    assert abs(tile.findChild(QObject, 'effectPreviewRendering').property('gain') - 1.8) < 1e-6
    root.setProperty('currentPage', 2)
    backend._put(battery=70)
    assert root.property('currentPage') == 2  # ordinary refresh must not hijack navigation
    backend._select_device('0x0008')
    backend._put(connected=True, power=True)
    backend._accept('effect', {'effect': {'slug': 'color-wheel', 'label': 'Color Wheel'},
                               'work_mode': 1, 'work_mode_source': 'readback',
                               'effect_source': 'readback'})
    app.processEvents()
    assert root.property('currentPage') == 0
    assert root.findChild(QObject, 'applyColorButton').property('highlighted')
    assert not main.property('visible')
    backend._select_device('0x000e')
    backend._put(connected=True, power=True)
    backend._accept('effect', {'effect': {'slug': 'red-flash', 'label': 'Red Flash'},
                               'effect_active': None, 'effect_source': 'saved',
                               'last_known_work_mode': 1})
    QTest.qWait(30)
    assert root.property('currentPage') == 1
    assert main.property('animationUrl').toString().endswith('red-flash.animated.webp')
    assert root.findChild(QObject, 'sphereStateLabel').property('text') == 'Last known: Red Flash'
    assert abs(main.property('gain') - 1.8) < 1e-6  # never borrow previous device brightness
    backend._accept('effect', {'effect': {'slug': 'red-flash', 'label': 'Red Flash'},
                               'work_mode': 1, 'work_mode_source': 'readback',
                               'effect_source': 'readback'})
    QTest.qWait(30)
    assert root.findChild(QObject, 'sphereStateLabel').property('text') == 'Red Flash'
    assert backend.previewStateSource == 'readback'
    for mode, label in ((0, 'Light on'), (2, 'Scene mode'), (3, 'Music mode'), (4, 'Mode 4')):
        backend._accept('effect', {'effect': {'slug': 'red-flash', 'label': 'Red Flash'},
                                   'work_mode': mode, 'work_mode_source': 'readback',
                                   'effect_source': 'readback'})
        QTest.qWait(30)
        assert not main.property('visible')
        assert root.findChild(QObject, 'sphereStateLabel').property('text') == label
    assert not any(call.method in ('lighting', 'effect.activate', 'control') for call in calls)
    backend.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', script], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
