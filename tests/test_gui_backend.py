"""Local socket contract checks for the optional desktop client."""

import json
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer

from sphere_mesh.gui.backend import (
    SphereBackend,
    hsv_to_mesh_hsl,
    mesh_hsl_to_hsv,
)


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def socket_path():
    with tempfile.TemporaryDirectory(prefix="sphere-gui-", dir="/tmp") as directory:
        yield Path(directory) / "sphere.sock"


def until(app, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    assert predicate()


@pytest.mark.parametrize(
    "hsv,expected_hsl",
    [
        ((65535, 0, 65535), (32768, 0, 65535)),
        ((65535, 21845, 0), (65535, 21845, 0)),
        ((32768, 43690, 32768), (24576, 43690, 21845)),
        ((0, 12345, 65535), (0, 12345, 0)),
    ],
)
def test_phone_compatible_hsv_to_mesh_hsl(hsv, expected_hsl):
    assert hsv_to_mesh_hsl(*hsv) == expected_hsl
    converted = mesh_hsl_to_hsv(dict(zip(
        ("lightness", "hue", "saturation"), expected_hsl
    )))
    assert converted["hue"] == hsv[1]
    assert abs(converted["value"] - hsv[0]) <= 1
    if hsv[0]:
        assert abs(converted["saturation"] - hsv[2]) <= 1


class ServiceRequest(dict):
    """Wire request plus local test history, without extra protocol keys."""


class FakeService:
    def __init__(self, path, handler):
        self.server = QLocalServer()
        assert self.server.listen(str(path))
        self.handler = handler
        self.requests = []
        self.sockets = []
        self.server.newConnection.connect(self._connect)

    def _connect(self):
        socket = self.server.nextPendingConnection()
        self.sockets.append(socket)
        socket.readyRead.connect(lambda: self._read(socket))

    def _read(self, socket):
        raw = bytes(socket.readAll())
        for line in raw.splitlines():
            request = ServiceRequest(json.loads(line))
            request.history = list(self.requests)
            self.requests.append(request)
            result = self.handler(request)
            if result is not None:
                envelope = {"version": 1, "id": request["id"], **result}
                socket.write(json.dumps(envelope).encode() + b"\n")

    def close(self):
        for socket in self.sockets:
            socket.abort()
        self.server.close()


CAPABILITIES = {
    "control": True,
    "lighting": {"lightness": True, "temperature": True, "hsl": True},
    "effects": True,
    "vendor_read": True,
}
EFFECTS = [
    {"slug": "aurora", "name": "colour_1", "label": "Aurora", "value": 1, "visible": True},
    {"slug": "hidden", "name": "colour_2", "label": "Hidden", "value": 2, "visible": False},
    {"slug": "color-wheel", "name": "colour_7", "label": "Color Wheel", "value": 7, "visible": True},
    {"slug": "red-flash", "name": "colour_8", "label": "Red Flash", "value": 8, "visible": True},
]


def standard_handler(request):
    method = request["method"]
    params = request["params"]
    if method == "device.list":
        result = {"devices": [{"device": "0x0002", "name": "ARI", "configured": True}]}
    elif method == "enrollment.status":
        result = {"active": False, "candidates": []}
    elif method == "status":
        result = {"connected": True, "generation": 1, "device": "0x0002", "capabilities": CAPABILITIES}
    elif method == "control":
        result = {"state": {"effective": params["command"] != "off"}}
    elif method == "lighting":
        command = params["command"]
        model = command.split("=")[0]
        value = command.partition("=")[2]
        state = (
            {"present": int(value or 30000)} if model == "lightness" else
            {"present": int(value or 4000)} if model == "temperature" else
            dict(zip(("lightness", "hue", "saturation"), map(int, value.split(","))))
            if model == "hsl" and value else
            {"lightness": 30000, "hue": 10000, "saturation": 40000}
        )
        result = {"state": state}
    elif method == "effect.list":
        result = {"effects": EFFECTS}
    elif method == "effect.get":
        activation = next((item for item in reversed(getattr(request, "history", []))
                           if item["method"] == "effect.activate"), None)
        selected = next((effect for effect in EFFECTS
                         if activation and effect["slug"] == activation["params"]["name"]), EFFECTS[0])
        result = {"effect": selected, "effect_active": True if activation else None,
                  "effect_source": "cached" if activation else "readback",
                  "verification": {"independent_readback": True}}
    elif method == "effect.activate":
        result = {"acknowledged": True}
    elif method == "vendor.read":
        result = {"datapoints": [{"id": 103, "value": 87}]}
    else:
        raise AssertionError(method)
    return {"ok": True, "result": result}


def test_status_refresh_and_mutations_use_versioned_socket(app, socket_path):
    service = FakeService(socket_path, standard_handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        assert backend.connected
        assert backend.capabilities["power"]
        assert backend.lightness == 30000
        assert backend.temperature is None
        assert not any(
            item["method"] == "lighting"
            and item["params"]["command"] == "temperature"
            for item in service.requests
        )
        assert backend.effect["slug"] == "aurora"
        assert [item["slug"] for item in backend.effects] == [
            "aurora",
            "red-flash",
        ]
        backend.activateEffect("hidden")
        app.processEvents()
        assert not any(item["method"] == "effect.activate" for item in service.requests)
        backend.activateEffect("aurora")
        until(app, lambda: not backend.pending)
        assert [item["method"] for item in service.requests[-2:]] == ["effect.activate", "effect.get"]
        backend.setLightness(22222)
        until(app, lambda: backend.lightness == 22222)
        assert all(item["version"] == 1 and isinstance(item["id"], int) for item in service.requests)
        diagnostics = json.loads(backend.diagnostics)
        assert diagnostics["device"] == "0x0002"
        assert "keys" not in diagnostics
    finally:
        backend.close()
        service.close()


@pytest.mark.parametrize(
    "effect,active,expected_flags",
    [
        (EFFECTS[0], True, (True, False)),
        (EFFECTS[2], True, (False, True)),
        (EFFECTS[0], False, (False, False)),
    ],
)
def test_startup_refresh_restores_active_mode(app, socket_path, effect, active, expected_flags):
    def handler(request):
        if request["method"] == "effect.get":
            return {"ok": True, "result": {
                "effect": effect, "work_mode": 1 if active else 0,
                "effect_active": active,
                "work_mode_source": "readback",
            }}
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        assert (backend.effectActive, backend.colorModeActive) == expected_flags
        assert backend.effect["slug"] == effect["slug"]
    finally:
        backend.close()
        service.close()


@pytest.mark.parametrize("effect_slug", ["aurora", "red-flash"])
def test_effect_and_color_brightness_send_only_hsl(app, socket_path, effect_slug):
    service = FakeService(socket_path, standard_handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        assert not backend.effectActive
        backend.activateEffect(effect_slug)
        until(app, lambda: not backend.pending)
        assert backend.effectActive

        until(app, lambda: backend._active is None and not backend._queue)
        before = len(service.requests)
        backend.setBrightness(24000)
        until(app, lambda: not backend.pending)
        writes = service.requests[before:]
        original_hsv = mesh_hsl_to_hsv({"lightness": 30000, "hue": 10000, "saturation": 40000})
        expected_hsl = hsv_to_mesh_hsl(24000, original_hsv["hue"], original_hsv["saturation"])
        assert [(item["method"], item["params"]) for item in writes if item["method"] != "lighting" or "=" in item["params"]["command"]] == [
            ("lighting", {"command": "hsl=" + ",".join(map(str, expected_hsl)), "device": "0x0002"}),
        ]
        assert backend.effectActive

        until(app, lambda: backend._active is None and not backend._queue)
        before = len(service.requests)
        backend.activateColor(65535, 0, 65535)
        until(app, lambda: not backend.pending)
        assert not backend.effectActive
        assert backend.colorModeActive
        writes = service.requests[before:]
        assert [(item["method"], item["params"]) for item in writes
                if item["method"] == "effect.activate"
                or (item["method"] == "lighting" and "=" in item["params"]["command"])] == [
            ("effect.activate", {"name": "color-wheel", "device": "0x0002"}),
            ("lighting", {"command": "hsl=32768,0,65535", "device": "0x0002"}),
        ]
        selected_hsv = dict(backend.hsv)
        before = len(service.requests)
        backend.setBrightness(18000)
        until(app, lambda: not backend.pending)
        writes = service.requests[before:]
        expected_hsl = hsv_to_mesh_hsl(
            18000, selected_hsv["hue"], selected_hsv["saturation"]
        )
        assert [(item["method"], item["params"]) for item in writes
                if item["method"] == "lighting" and "=" in item["params"]["command"]] == [
            ("lighting", {"command": "hsl=" + ",".join(map(str, expected_hsl)), "device": "0x0002"}),
        ]
        assert not any(item["method"] == "effect.activate" for item in writes)
        assert backend.colorModeActive
    finally:
        backend.close()
        service.close()


def test_failed_brightness_update_clears_known_mode(app, socket_path):
    brightness_writes = 0

    def handler(request):
        nonlocal brightness_writes
        if (request["method"] == "lighting"
                and request["params"]["command"].startswith("hsl=")):
            brightness_writes += 1
            return {"ok": False, "error": {
                "code": "unavailable", "message": "color write timed out",
            }}
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        backend.activateEffect("aurora")
        until(app, lambda: not backend.pending)
        backend.setBrightness(24000)
        until(app, lambda: not backend.pending)
        assert not backend.effectActive
        assert "color write timed out" in backend.errorMessage
        assert brightness_writes == 1
        assert sum(item["method"] == "effect.activate" for item in service.requests) == 1
    finally:
        backend.close()
        service.close()


def test_reconnect_forgets_color_mode(app, socket_path):
    service = FakeService(socket_path, standard_handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        backend.activateColor(32768, 12000, 40000)
        until(app, lambda: not backend.pending)
        assert backend.colorModeActive
        backend._status_result({
            "connected": True, "generation": 2,
            "device": "0x0002", "capabilities": CAPABILITIES,
        }, None)
        assert not backend.colorModeActive
        assert not backend.effectActive
    finally:
        backend.close()
        service.close()


def test_partial_effect_failure_reads_back_without_retry(app, socket_path):
    def handler(request):
        if request["method"] == "effect.activate":
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "effect write timed out",
                    "details": {"completed_steps": ["mode_transport_acknowledged"], "result_uncertain": True},
                },
            }
        if request["method"] == "effect.get":
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "readback failed",
                },
            }
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        backend.activateEffect("aurora")
        until(app, lambda: not backend.pending)
        assert backend.errorDetails["result_uncertain"] is True
        assert backend.errorDetails["readback_error"] == {
            "code": "unavailable",
            "message": "readback failed",
        }
        assert "effect write timed out" in backend.errorMessage
        assert [item["method"] for item in service.requests[-2:]] == ["effect.activate", "effect.get"]
        assert sum(item["method"] == "effect.activate" for item in service.requests) == 1
    finally:
        backend.close()
        service.close()


def test_battery_protocol_error_does_not_block_controls(app, socket_path):
    def handler(request):
        if request["method"] == "vendor.read":
            return {
                "ok": False,
                "error": {
                    "code": "protocol_error",
                    "message": "unsupported vendor DATA command type 0x00",
                },
            }
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.effect.get("slug") == "aurora")
        until(app, lambda: backend._active is None and not backend._queue)
        assert backend.connected
        assert backend.battery is None
        assert backend.errorMessage == ""
        assert backend.serviceAvailable and backend.serviceState == "ready"
    finally:
        backend.close()
        service.close()


def test_missing_daemon_is_reported(app, tmp_path):
    backend = SphereBackend(tmp_path / "missing.sock", poll_waiting_ms=60000)
    try:
        until(app, lambda: backend._status_due is False and backend._next_id > 1)
        assert backend.connectionState == "unavailable"
        assert not backend.connected
        assert not backend.serviceAvailable
        assert backend.serviceState == "unavailable"
        assert backend.serviceError
    finally:
        backend.close()


def test_preset_catalog_resolves_existing_media_and_handles_missing(tmp_path, monkeypatch):
    from sphere_mesh.gui import backend as gui_backend

    directory = tmp_path / "assets" / "presets"
    directory.mkdir(parents=True)
    (directory / "aurora.webp").write_bytes(b"preview")
    (directory / "catalog.json").write_text(json.dumps({
        "aurora": {"category": "animation", "still": "aurora.webp",
                   "animation": "missing.gif"},
        "bad": {"category": "pattern", "still": "../outside.webp"},
    }))
    monkeypatch.setattr(gui_backend, "files", lambda package: tmp_path)
    catalog = gui_backend._preset_catalog()
    assert catalog["aurora"]["category"] == "animation"
    assert catalog["aurora"]["stillUrl"].startswith("file:")
    assert catalog["aurora"]["animationUrl"] == ""
    assert catalog["bad"]["stillUrl"] == ""
    (directory / "catalog.json").write_text("invalid")
    assert gui_backend._preset_catalog() == {}


def test_device_switch_targets_requests_and_discards_old_response(app, socket_path):
    held = []

    def handler(request):
        if request['method'] == 'device.list':
            return {'ok': True, 'result': {'devices': [
                {'device': '0x0002', 'name': 'ARI', 'configured': True},
                {'device': '0x0008', 'name': 'BEN', 'configured': True},
            ]}}
        if request['method'] == 'status':
            return {'ok': True, 'result': {'connected': True, 'generation': 1,
                                          'capabilities': CAPABILITIES}}
        if request['method'] == 'control' and held == ['hold']:
            held.append(request)
            return None
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        until(app, lambda: backend._active is None and not backend._queue)
        assert backend.selectedName == 'ARI'
        accepted = []
        original = backend._accept
        backend._accept = lambda field, value: (accepted.append(value), original(field, value))[-1]
        held.append('hold')
        backend.refresh()
        until(app, lambda: len(held) == 2)
        assert held[1]['params']['device'] == '0x0002'
        backend.selectDevice('0x0008')
        assert backend.power is None and backend.battery is None
        assert backend.selectedName == 'BEN'
        service.sockets[-1].write(json.dumps({
            'version': 1, 'id': held[1]['id'], 'ok': True,
            'result': {'state': {'effective': False}, 'old_device_response': True},
        }).encode() + b'\n')
        until(app, lambda: backend.battery == 87 and backend.connected)
        assert not any(value.get('old_device_response') for value in accepted)
        backend.setPower(False)
        until(app, lambda: not backend.pending)
        writes = [item for item in service.requests if item['method'] == 'control'
                  and item['params']['command'] == 'off']
        assert writes[-1]['params']['device'] == '0x0008'
        for item in service.requests:
            if item['method'] in {'status', 'control', 'lighting', 'effect.get', 'vendor.read'}:
                assert item['params'].get('device') in {'0x0002', '0x0008'}
    finally:
        backend.close()
        service.close()


def test_gui_automatic_enrollment_and_close_stops_pending_start(app, socket_path):
    start_request = []
    active = [False]

    def handler(request):
        if request['method'] == 'enrollment.start':
            start_request.append(request)
            return None
        if request['method'] == 'enrollment.stop':
            active[0] = False
            return {'ok': True, 'result': {'active': False, 'candidates': []}}
        if request['method'] == 'enrollment.status':
            return {'ok': True, 'result': {'active': active[0], 'candidates': []}}
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        until(app, lambda: backend._active is None and not backend._queue)
        backend.startEnrollment()
        until(app, lambda: len(start_request) == 1)
        assert start_request[0]['params'] == {'duration': 120, 'require_names': False}
        backend.stopEnrollment()
        active[0] = True
        service.sockets[-1].write(json.dumps({
            'version': 1, 'id': start_request[0]['id'], 'ok': True,
            'result': {'active': True, 'candidates': []},
        }).encode() + b'\n')
        until(app, lambda: any(item['method'] == 'enrollment.stop' for item in service.requests))
        until(app, lambda: not backend.managementPending)
        assert not backend.enrollment['active']
    finally:
        backend.close()
        service.close()


def test_candidate_list_changes_only_when_candidates_change(app, tmp_path):
    backend = SphereBackend(tmp_path / 'missing.sock', start=False)
    signals = []
    backend.candidatesChanged.connect(lambda: signals.append(True))
    candidates = [{'candidate': 'example', 'name': '', 'state': 'awaiting_name'}]
    try:
        backend._accept_enrollment({'active': True, 'remaining_seconds': 100, 'candidates': candidates})
        backend._accept_enrollment({'active': True, 'remaining_seconds': 99, 'candidates': candidates})
        assert len(signals) == 1
    finally:
        backend.close()


@pytest.mark.parametrize("operation,expected", [
    (lambda backend: backend.setPower(False),
     [("control", "off"), ("control", "read")]),
    (lambda backend: backend.setHsl(20000, 10000, 30000),
     [("lighting", "hsl=20000,10000,30000"), ("lighting", "hsl")]),
])
def test_ordinary_change_reads_back_only_changed_state(app, socket_path, operation, expected):
    service = FakeService(socket_path, standard_handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        until(app, lambda: backend._active is None and not backend._queue)
        before = len(service.requests)
        operation(backend)
        until(app, lambda: not backend.pending and backend._active is None and not backend._queue)
        assert [(request["method"], request["params"].get("command"))
                for request in service.requests[before:]] == expected
    finally:
        backend.close()
        service.close()


def test_daemon_loss_and_restart_are_distinct_from_mesh_failure(app, socket_path):
    service = FakeService(socket_path, standard_handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000, poll_waiting_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        until(app, lambda: backend._active is None and not backend._queue)
        assert backend.serviceAvailable and backend.serviceState == 'ready'
        service.close()
        backend._poll_status()
        until(app, lambda: backend.serviceState == 'unavailable')
        assert not backend.connected
        assert backend.serviceError
        # An idle failure is visible even without a pending user command.
        assert not backend.pending
        until(app, lambda: backend._active is None and not backend._queue)
        service = FakeService(socket_path, standard_handler)
        backend._poll_status()
        until(app, lambda: backend.connected and backend.serviceState == 'ready')
        assert backend.serviceError == ''
        until(app, lambda: backend._active is None and not backend._queue)
        service.handler = lambda request: (
            {'ok': True, 'result': {'connected': False, 'generation': 2,
                                  'last_failure': 'Bluetooth Mesh is unavailable'}}
            if request['method'] == 'status' else standard_handler(request)
        )
        backend._poll_status()
        until(app, lambda: backend.connectionState == 'reconnecting')
        assert backend.serviceAvailable and backend.serviceState == 'ready'
        assert not backend.connected
    finally:
        backend.close()
        service.close()


def test_daemon_timeout_reports_uncertainty_without_repeating_write(app, socket_path):
    def handler(request):
        if request['method'] == 'control' and request['params']['command'] == 'off':
            return None
        return standard_handler(request)

    service = FakeService(socket_path, handler)
    backend = SphereBackend(socket_path, poll_connected_ms=60000)
    try:
        until(app, lambda: backend.battery == 87)
        until(app, lambda: backend._active is None and not backend._queue)
        backend.setPower(False)
        until(app, lambda: any(item['method'] == 'control' and
                              item['params']['command'] == 'off' for item in service.requests))
        backend._on_timeout()
        assert backend.serviceState == 'unresponsive'
        assert not backend.serviceAvailable and not backend.connected
        assert 'uncertain' in backend.serviceError
        until(app, lambda: backend.connected)
        assert sum(item['method'] == 'control' and item['params']['command'] == 'off'
                   for item in service.requests) == 1
    finally:
        backend.close()
        service.close()


def test_invalid_daemon_response_is_not_reported_as_bluetooth_failure(app, socket_path):
    service = FakeService(socket_path, lambda request: {'ok': True, 'result': 'invalid'})
    backend = SphereBackend(socket_path, poll_waiting_ms=60000)
    try:
        until(app, lambda: backend.serviceState == 'invalid_response')
        assert not backend.serviceAvailable and not backend.connected
        assert 'invalid result' in backend.serviceError
    finally:
        backend.close()
        service.close()


def test_saved_effect_metadata_is_device_scoped_and_explicitly_last_known(app, tmp_path):
    backend = SphereBackend(tmp_path / 'missing.sock', start=False)
    try:
        backend._select_device('0x0002')
        backend._accept('effect', {'effect': EFFECTS[0], 'effect_source': 'saved',
                                   'effect_active': None, 'last_known_work_mode': 1})
        assert backend.effectActive and backend.previewKind == 'effect'
        assert backend.previewStateSource == 'saved'
        backend._select_device('0x0008')
        assert backend.effect == {} and backend.previewKind == 'unknown'
        assert backend.workMode is None
        assert not backend.effectActive
        backend._accept('effect', {'effect': EFFECTS[2], 'effect_source': 'cached',
                                   'effect_active': True, 'work_mode': 1})
        assert backend.colorModeActive and not backend.effectActive
        assert backend.previewKind == 'color'
    finally:
        backend.close()


@pytest.mark.parametrize('mode,kind,active', [
    (0, 'light', False), (1, 'effect', True),
    (2, 'unknown', False), (3, 'unknown', False),
    (4, 'unknown', False),
])
def test_current_work_mode_controls_preview_over_stale_summary(app, tmp_path, mode, kind, active):
    backend = SphereBackend(tmp_path / 'missing.sock', start=False)
    try:
        backend._accept('effect', {
            'effect': EFFECTS[0], 'effect_source': 'readback',
            'work_mode': mode, 'work_mode_source': 'readback',
            'effect_active': not active, 'last_known_work_mode': 1,
        })
        assert backend.workMode == mode
        assert backend.previewKind == kind
        assert backend.effectActive is active
        assert backend.previewStateSource == 'readback'
        assert not backend.colorModeActive
    finally:
        backend.close()


def test_fresh_work_mode_and_scene_replace_saved_preview_atomically(app, tmp_path):
    backend = SphereBackend(tmp_path / 'missing.sock', start=False)
    try:
        backend._accept('effect', {
            'effect': EFFECTS[0], 'effect_source': 'saved',
            'last_known_work_mode': 1,
        })
        observed = []
        backend.stateChanged.connect(lambda: observed.append(
            (backend.effect.get('slug'), backend.workMode, backend.previewKind)
        ))
        backend._accept('effect', {
            'effect': EFFECTS[2], 'effect_source': 'readback',
            'work_mode': 0, 'work_mode_source': 'readback',
        })
        assert observed == [('color-wheel', 0, 'light')]
        assert backend.previewStateSource == 'readback'
        backend._select_device('0x0008')
        assert backend.workMode is None
    finally:
        backend.close()
