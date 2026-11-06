"""Qt state model and local socket client for the Sphere dashboard."""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from importlib.resources import files
from typing import Callable
from ..paths import socket_path as default_socket_path

try:
    from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalSocket
except ImportError as exc:
    raise RuntimeError(
        "The desktop app requires PySide6; install spherosphere[gui]"
    ) from exc


MAX_RESPONSE_BYTES = 64 * 1024
MAX_LIGHT_VALUE = 0xFFFF


def _preset_catalog() -> dict:
    """Load bundled preview metadata, tolerating missing optional media."""
    directory = files("spherosphere.gui").joinpath("assets", "presets")
    try:
        catalog = json.loads(directory.joinpath("catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(catalog, dict):
        return {}
    result = {}
    for slug, metadata in catalog.items():
        if not isinstance(slug, str) or not isinstance(metadata, dict):
            continue
        category = metadata.get("category")
        if category not in ("pattern", "animation"):
            continue
        entry = {"category": category, "stillUrl": "", "animationUrl": ""}
        for key, target in (("still", "stillUrl"), ("animation", "animationUrl")):
            name = metadata.get(key)
            if isinstance(name, str) and Path(name).name == name:
                resource = directory.joinpath(name)
                if resource.is_file():
                    entry[target] = QUrl.fromLocalFile(str(resource)).toString()
        result[slug] = entry
    return result


def mesh_hsl_to_hsv(state: object) -> dict[str, int]:
    if not isinstance(state, dict):
        return {}
    values = [state.get(name) for name in ("lightness", "hue", "saturation")]
    if not all(type(value) is int and 0 <= value <= MAX_LIGHT_VALUE for value in values):
        return {}
    lightness, hue, saturation = values
    lightness_unit = lightness / MAX_LIGHT_VALUE
    saturation_unit = saturation / MAX_LIGHT_VALUE
    value_unit = lightness_unit + saturation_unit * min(
        lightness_unit, 1 - lightness_unit
    )
    hsv_saturation = (
        0.0
        if value_unit == 0
        else 2 * (1 - lightness_unit / value_unit)
    )
    return {
        "value": round(value_unit * MAX_LIGHT_VALUE),
        "hue": hue,
        "saturation": round(hsv_saturation * MAX_LIGHT_VALUE),
    }


def hsv_to_mesh_hsl(
    value: int, hue: int, saturation: int
) -> tuple[int, int, int]:
    values = (value, hue, saturation)
    if not all(
        type(component) is int and 0 <= component <= MAX_LIGHT_VALUE
        for component in values
    ):
        raise ValueError("HSV components must be uint16 values")
    value_unit = value / MAX_LIGHT_VALUE
    saturation_unit = saturation / MAX_LIGHT_VALUE
    lightness_unit = value_unit * (1 - saturation_unit / 2)
    hsl_saturation = (
        0.0
        if lightness_unit in (0.0, 1.0)
        else (value_unit - lightness_unit)
        / min(lightness_unit, 1 - lightness_unit)
    )
    return (
        round(lightness_unit * MAX_LIGHT_VALUE),
        hue,
        round(hsl_saturation * MAX_LIGHT_VALUE),
    )


@dataclass
class _Request:
    method: str
    params: dict
    callback: Callable[[dict | None, dict | None], None]
    generation: int = 0
    timeout_ms: int = 15000
    target: str | None = None


class SphereBackend(QObject):
    """QML-facing service model; all I/O stays on the Qt event loop."""

    stateChanged = Signal()
    inventoryChanged = Signal()
    candidatesChanged = Signal()
    effectsChanged = Signal()

    def __init__(
        self,
        socket_path: str | Path | None = None,
        parent: QObject | None = None,
        *,
        start: bool = True,
        poll_connected_ms: int = 10000,
        poll_waiting_ms: int = 2000,
    ) -> None:
        super().__init__(parent)
        self._preset_catalog = _preset_catalog()
        self._socket_path = str(Path(socket_path if socket_path is not None
                                     else default_socket_path()).expanduser().resolve())
        self._queue: deque[_Request] = deque()
        self._active: _Request | None = None
        self._socket: QLocalSocket | None = None
        self._next_id = 1
        self._active_id = 0
        self._buffer = bytearray()
        self._refresh_generation = 0
        self._refresh_queued = False
        self._poll_connected_ms = poll_connected_ms
        self._poll_waiting_ms = poll_waiting_ms
        self._status_due = False
        self._management_status_due = False
        self._stop_enrollment_requested = False
        self._socket_identity: tuple[int, int] | None = None
        self._state: dict = {
            "connectionState": "unavailable",
            "connected": False,
            "pending": False,
            "pendingAction": "",
            "errorMessage": "",
            "errorDetails": {},
            "capabilities": {},
            "power": None,
            "lightness": None,
            "temperature": None,
            "hsl": {},
            "effect": {},
            "effectActive": False,
            "colorModeActive": False,
            "workMode": None,
            "previewKind": "unknown",
            "previewStateSource": "unknown",
            "effects": [],
            "battery": None,
            "generation": 0,
            "device": "",
            "lastFailure": "",
            "devices": [],
            "serviceAvailable": False,
            "serviceState": "connecting",
            "serviceError": "",
            "managementPending": False,
            "managementError": "",
            "enrollment": {"active": False, "candidates": []},
            "enrollmentCandidates": [],
        }
        self._verification: dict = {}
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._poll_status)
        self._management_poll = QTimer(self)
        self._management_poll.setSingleShot(True)
        self._management_poll.timeout.connect(self._poll_enrollment)
        if start:
            QTimer.singleShot(0, self._poll_status)

    def _get(self, key: str):
        return self._state[key]

    connectionState = Property(str, lambda self: self._get("connectionState"), notify=stateChanged)
    connected = Property(bool, lambda self: self._get("connected"), notify=stateChanged)
    pending = Property(bool, lambda self: self._get("pending"), notify=stateChanged)
    pendingAction = Property(str, lambda self: self._get("pendingAction"), notify=stateChanged)
    errorMessage = Property(str, lambda self: self._get("errorMessage"), notify=stateChanged)
    errorDetails = Property("QVariant", lambda self: self._get("errorDetails"), notify=stateChanged)
    capabilities = Property("QVariant", lambda self: self._get("capabilities"), notify=stateChanged)
    power = Property("QVariant", lambda self: self._get("power"), notify=stateChanged)
    lightness = Property("QVariant", lambda self: self._get("lightness"), notify=stateChanged)
    temperature = Property("QVariant", lambda self: self._get("temperature"), notify=stateChanged)
    hsl = Property("QVariant", lambda self: self._get("hsl"), notify=stateChanged)
    hsv = Property(
        "QVariant",
        lambda self: mesh_hsl_to_hsv(self._get("hsl")),
        notify=stateChanged,
    )
    effect = Property("QVariant", lambda self: self._get("effect"), notify=stateChanged)
    effectActive = Property(bool, lambda self: self._get("effectActive"), notify=stateChanged)
    colorModeActive = Property(bool, lambda self: self._get("colorModeActive"), notify=stateChanged)
    workMode = Property("QVariant", lambda self: self._get("workMode"), notify=stateChanged)
    previewKind = Property(str, lambda self: self._get("previewKind"), notify=stateChanged)
    previewStateSource = Property(str, lambda self: self._get("previewStateSource"), notify=stateChanged)
    effects = Property("QVariant", lambda self: self._get("effects"), notify=effectsChanged)
    battery = Property("QVariant", lambda self: self._get("battery"), notify=stateChanged)
    generation = Property(int, lambda self: self._get("generation"), notify=stateChanged)
    device = Property(str, lambda self: self._get("device"), notify=stateChanged)
    selectedDevice = Property(str, lambda self: self._get("device"), notify=stateChanged)
    selectedName = Property(str, lambda self: next(
        (entry["name"] for entry in self._state["devices"]
         if entry["device"] == self._state["device"]), ""), notify=stateChanged)
    devices = Property("QVariant", lambda self: self._get("devices"), notify=inventoryChanged)
    enrollmentCandidates = Property("QVariant", lambda self: self._get("enrollmentCandidates"), notify=candidatesChanged)
    serviceAvailable = Property(bool, lambda self: self._get("serviceAvailable"), notify=stateChanged)
    serviceState = Property(str, lambda self: self._get("serviceState"), notify=stateChanged)
    serviceError = Property(str, lambda self: self._get("serviceError"), notify=stateChanged)
    managementPending = Property(bool, lambda self: self._get("managementPending"), notify=stateChanged)
    managementError = Property(str, lambda self: self._get("managementError"), notify=stateChanged)
    enrollment = Property("QVariant", lambda self: self._get("enrollment"), notify=stateChanged)
    lastFailure = Property(str, lambda self: self._get("lastFailure"), notify=stateChanged)
    socketPath = Property(str, lambda self: self._socket_path, constant=True)
    diagnostics = Property(str, lambda self: self._diagnostics(), notify=stateChanged)

    def _put(self, **values) -> None:
        changed = False
        inventory_changed = "devices" in values and values["devices"] != self._state["devices"]
        effects_changed = "effects" in values and values["effects"] != self._state["effects"]
        candidates_changed = ("enrollmentCandidates" in values
                              and values["enrollmentCandidates"] != self._state["enrollmentCandidates"])
        for key, value in values.items():
            if self._state[key] != value:
                self._state[key] = value
                changed = True
        if changed:
            self.stateChanged.emit()
        if inventory_changed:
            self.inventoryChanged.emit()
        if candidates_changed:
            self.candidatesChanged.emit()
        if effects_changed:
            self.effectsChanged.emit()

    def _diagnostics(self) -> str:
        data = {
            "socket": self._socket_path,
            "connection": self._state["connectionState"],
            "daemon": {"state": self._state["serviceState"],
                       "error": self._state["serviceError"]},
            "generation": self._state["generation"],
            "device": self._state["device"],
            "last_failure": self._state["lastFailure"],
            "capabilities": self._state["capabilities"],
            "preview_source": self._state["previewStateSource"],
            "work_mode": self._state["workMode"],
            "pending_action": self._state["pendingAction"],
            "error": {
                "message": self._state["errorMessage"],
                "details": self._state["errorDetails"],
            },
            "verification": self._verification,
        }
        return json.dumps(data, indent=2, sort_keys=True)

    @Slot()
    def copyDiagnostics(self) -> None:
        app = QGuiApplication.instance()
        if app is not None and app.clipboard() is not None:
            app.clipboard().setText(self._diagnostics())

    @Slot()
    def clearError(self) -> None:
        self._put(errorMessage="", errorDetails={})

    def _error(self, error: dict, *, action: str = "") -> None:
        message = str(error.get("message", "Unknown service error"))
        if action:
            message = f"{action}: {message}"
        self._put(errorMessage=message, errorDetails=error.get("details") or {})

    def _enqueue(self, request: _Request) -> None:
        if request.method in {"status", "control", "lighting", "vendor.read",
                              "effect.get", "effect.activate", "effect.select",
                              "countdown.get", "countdown.set", "countdown.cancel"}:
            target = self._state["device"]
            if target:
                request.target = target
                request.params = {**request.params, "device": target}
        self._queue.append(request)
        self._pump()

    def _pump(self) -> None:
        if self._active is not None or not self._queue:
            return
        request = self._queue.popleft()
        self._active = request
        self._active_id = self._next_id
        self._next_id += 1
        self._buffer.clear()
        socket = QLocalSocket(self)
        self._socket = socket
        socket.connected.connect(self._on_connected)
        socket.readyRead.connect(self._on_read)
        socket.errorOccurred.connect(self._on_socket_error)
        self._timeout.start(request.timeout_ms)
        socket.connectToServer(self._socket_path)

    def _on_connected(self) -> None:
        request = self._active
        socket = self._socket
        if request is None or socket is None:
            return
        payload = {
            "version": 1,
            "id": self._active_id,
            "method": request.method,
            "params": request.params,
        }
        socket.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")

    def _on_read(self) -> None:
        socket = self._socket
        if socket is None:
            return
        self._buffer.extend(socket.readAll().data())
        if len(self._buffer) > MAX_RESPONSE_BYTES:
            self._finish(None, {"code": "protocol_error", "message": "Service response is too large"}, service_failure="invalid_response")
            return
        if b"\n" not in self._buffer:
            return
        raw, _, extra = self._buffer.partition(b"\n")
        if extra:
            self._finish(None, {"code": "protocol_error", "message": "Service sent more than one response"}, service_failure="invalid_response")
            return
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            self._finish(None, {"code": "protocol_error", "message": "Service returned invalid JSON"}, service_failure="invalid_response")
            return
        if (
            not isinstance(response, dict)
            or response.get("version") != 1
            or response.get("id") != self._active_id
            or type(response.get("ok")) is not bool
        ):
            self._finish(None, {"code": "protocol_error", "message": "Service returned an invalid response"}, service_failure="invalid_response")
            return
        if response["ok"]:
            result = response.get("result")
            if not isinstance(result, dict):
                self._finish(None, {"code": "protocol_error", "message": "Service returned an invalid result"}, service_failure="invalid_response")
            else:
                self._service_replied()
                self._finish(result, None)
        else:
            error = response.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                self._finish(None, {"code": "protocol_error", "message": "Service returned an invalid error"}, service_failure="invalid_response")
                return
            self._service_replied()
            self._finish(None, error)

    def _service_replied(self) -> None:
        """A valid API error still proves the daemon itself is reachable."""
        self._put(serviceAvailable=True, serviceState="ready", serviceError="")

    def _on_socket_error(self, _error) -> None:
        socket = self._socket
        if socket is not None and self._active is not None:
            self._finish(None, {"code": "transport_error", "message": socket.errorString()}, service_failure="unavailable")

    def _on_timeout(self) -> None:
        if self._active is not None:
            self._finish(None, {"code": "timeout", "message": "Service did not respond in time; the result is uncertain"}, service_failure="unresponsive")

    def _finish(self, result: dict | None, error: dict | None, *,
                service_failure: str | None = None) -> None:
        request, self._active = self._active, None
        socket, self._socket = self._socket, None
        self._timeout.stop()
        if socket is not None:
            socket.abort()
            socket.deleteLater()
        if request is None:
            return
        if service_failure:
            self._put(serviceAvailable=False, serviceState=service_failure,
                      serviceError=(error or {}).get("message", ""),
                      connectionState="unavailable", connected=False,
                      effectActive=False, colorModeActive=False)
            self._schedule_poll()
        if request.target is not None and request.target != self._state["device"]:
            QTimer.singleShot(0, self._pump)
            return
        request.callback(result, error)
        QTimer.singleShot(0, self._pump)

    def _poll_status(self) -> None:
        self._poll.stop()
        if self._status_due:
            return
        self._status_due = True
        self._enqueue(_Request("device.list", {}, self._inventory_result, timeout_ms=4000))

    def _inventory_result(self, result: dict | None, error: dict | None) -> None:
        if error is not None:
            self._status_result(None, error)
            return
        entries = [entry for entry in (result or {}).get("devices", [])
                   if isinstance(entry, dict) and isinstance(entry.get("device"), str)]
        entries = [{**entry, "name": entry.get("name") or "Unnamed Sphere"}
                   for entry in entries]
        self._put(devices=entries, serviceAvailable=True)
        addresses = {entry["device"] for entry in entries}
        if self._state["device"] not in addresses:
            self._select_device(entries[0]["device"] if entries else "")
        method = "status" if self._state["device"] else "health"
        self._enqueue(_Request(method, {}, self._status_result, timeout_ms=4000))

    def _select_device(self, address: str) -> None:
        """Discard old-device displays and unsent reads before selecting another."""
        self._refresh_generation += 1
        self._refresh_queued = False
        self._queue = deque(request for request in self._queue if request.target is None)
        self._verification = {}
        self._put(device=address, power=None, lightness=None, temperature=None,
                  hsl={}, effect={}, effectActive=False, colorModeActive=False,
                  workMode=None,
                  previewKind="unknown", previewStateSource="unknown",
                  battery=None, capabilities={}, connected=False,
                  errorMessage="", errorDetails={})

    @Slot(str)
    def selectDevice(self, address: str) -> None:
        if self._state["pending"] or self._state["enrollment"].get("active"):
            return
        if address == self._state["device"]:
            return
        if not any(entry["device"] == address for entry in self._state["devices"]):
            return
        self._select_device(address)
        # A superseded poll callback may be discarded after a selection.
        self._status_due = False
        self._poll_status()

    @Slot()
    def refreshDevices(self) -> None:
        self._poll_status()
        self._poll_enrollment()

    def _accept_enrollment(self, result: dict) -> None:
        was_active = self._state["enrollment"].get("active")
        self._put(enrollment=result, serviceAvailable=True,
                  enrollmentCandidates=result.get("candidates", []))
        if result.get("active"):
            self._refresh_generation += 1
            self._refresh_queued = False
            self._queue = deque(request for request in self._queue if request.target is None)
            self._status_due = False
            self._put(connected=False, effectActive=False, colorModeActive=False)
            self._management_poll.start(1000)
        elif was_active:
            self._management_poll.stop()
            self._poll_status()

    def _poll_enrollment(self) -> None:
        if self._management_status_due:
            return
        self._management_status_due = True

        def done(result, error):
            self._management_status_due = False
            if result is not None:
                self._accept_enrollment(result)
            elif error is not None:
                self._put(managementError=error.get("message", "Cannot read enrollment status"))
                if self._state["enrollment"].get("active"):
                    self._management_poll.start(2000)

        self._enqueue(_Request("enrollment.status", {}, done, timeout_ms=4000))

    def _manage(self, method: str, params: dict, *, enrollment_result=False) -> None:
        if self._state["managementPending"] or self._state["pending"]:
            return
        self._put(managementPending=True, managementError="")

        def done(result, error):
            self._put(managementPending=False)
            if error is not None:
                self._put(managementError=error.get("message", "Sphere management failed"))
            elif result is not None and enrollment_result:
                self._accept_enrollment(result)
            if self._stop_enrollment_requested:
                self._stop_enrollment_requested = False
                self.stopEnrollment()
            self._poll_status()
            # Completed stop/start/name calls already carry a progress snapshot.
            if not enrollment_result or error is not None:
                self._poll_enrollment()

        self._enqueue(_Request(method, params, done, timeout_ms=180000
                               if method == "enrollment.stop" else 15000))

    @Slot()
    def startEnrollment(self) -> None:
        self._manage("enrollment.start", {"duration": 120, "require_names": False},
                     enrollment_result=True)

    @Slot()
    def stopEnrollment(self) -> None:
        if self._state["managementPending"]:
            self._stop_enrollment_requested = True
            return
        if self._state["enrollment"].get("active"):
            self._manage("enrollment.stop", {}, enrollment_result=True)

    @Slot(str, str)
    def nameCandidate(self, candidate: str, name: str) -> None:
        if name.strip():
            self._manage("enrollment.name", {"candidate": candidate, "name": name.strip()},
                         enrollment_result=True)

    @Slot(str, str)
    def renameDevice(self, address: str, name: str) -> None:
        if name.strip():
            self._manage("device.rename", {"device": address, "name": name.strip()})

    @Slot(str)
    def forgetDevice(self, address: str) -> None:
        self._manage("device.forget", {"device": address})

    def _status_result(self, result: dict | None, error: dict | None) -> None:
        self._status_due = False
        if error is not None:
            # Only socket/protocol failures make the daemon unavailable.
            # A valid API failure may describe Bluetooth or device state.
            self._put(connectionState="reconnecting" if self._state["serviceAvailable"] else "unavailable", connected=False,
                      effectActive=False, colorModeActive=False)
            if self._state["pending"]:
                self._error(error, action=self._state["pendingAction"])
            self._schedule_poll()
            return
        assert result is not None
        connected = (result.get("connected") is True and bool(self._state["device"])
                     and not self._state["enrollment"].get("active"))
        generation = result.get("generation", 0)
        if type(generation) is not int:
            generation = 0
        changed_generation = generation != self._state["generation"]
        try:
            socket_stat = os.stat(self._socket_path)
            socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
        except OSError:
            socket_identity = None
        replaced_daemon = (
            self._socket_identity is not None
            and socket_identity != self._socket_identity
        )
        if not connected or changed_generation or replaced_daemon:
            self._put(effectActive=False, colorModeActive=False,
                      workMode=None, previewKind="unknown", previewStateSource="unknown")
        self._socket_identity = socket_identity
        was_connected = self._state["connected"]
        self._put(
            connectionState="connected" if connected else "reconnecting",
            connected=connected,
            serviceAvailable=True,
            generation=generation,
            lastFailure=str(result.get("last_failure") or ""),
            capabilities=self._flatten_capabilities(result.get("capabilities")),
        )
        if connected and (changed_generation or replaced_daemon or not was_connected):
            self.refresh()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        self._poll.start(self._poll_connected_ms if self._state["connected"] else self._poll_waiting_ms)

    @staticmethod
    def _flatten_capabilities(value: object) -> dict:
        if not isinstance(value, dict):
            return {}
        lighting = value.get("lighting")
        if not isinstance(lighting, dict):
            lighting = {}
        return {
            "power": value.get("control") is True,
            "lightness": lighting.get("lightness") is True,
            "temperature": lighting.get("temperature") is True,
            "hsl": lighting.get("hsl") is True,
            "effects": value.get("effects") is True,
            "vendor_read": value.get("vendor_read") is True,
        }

    @Slot()
    def refresh(self) -> None:
        self._refresh_generation += 1
        if self._refresh_queued:
            return
        self._refresh_queued = True
        self._refresh_next()

    def _refresh_next(self) -> None:
        if not self._state["connected"]:
            self._refresh_queued = False
            self._poll_status()
            return
        generation = self._refresh_generation
        capabilities = self._state["capabilities"]
        operations: list[tuple[str, dict, str]] = []
        if capabilities.get("power"):
            operations.append(("control", {"command": "read"}, "power"))
        if capabilities.get("effects"):
            operations.extend((("effect.list", {}, "effects"), ("effect.get", {}, "effect")))
        for model in ("lightness", "hsl"):
            if capabilities.get(model):
                operations.append(("lighting", {"command": model}, model))
        if capabilities.get("vendor_read"):
            operations.append(("vendor.read", {"dp_ids": [103]}, "battery"))

        def next_step(index: int) -> None:
            if generation != self._refresh_generation:
                self._refresh_next()
                return
            if index >= len(operations):
                self._refresh_queued = False
                return
            method, params, field = operations[index]

            def done(result: dict | None, error: dict | None) -> None:
                if generation != self._refresh_generation:
                    self._refresh_next()
                    return
                if result is not None:
                    self._accept(field, result)
                elif (
                    error is not None
                    and field != "battery"
                    and error.get("code") != "unavailable"
                ):
                    self._error(error, action=f"Reading {field}")
                next_step(index + 1)

            self._enqueue(_Request(method, params, done, generation))

        next_step(0)

    def _accept(self, field: str, result: dict) -> None:
        verification = result.get("verification")
        if isinstance(verification, dict):
            self._verification[field] = verification
            self.stateChanged.emit()
        state = result.get("state", {})
        if field == "power" and isinstance(state, dict):
            self._put(power=state.get("effective"))
        elif field == "lightness" and isinstance(state, dict):
            self._put(lightness=state.get("present"))
        elif field == "temperature" and isinstance(state, dict):
            self._put(temperature=state.get("present"))
        elif field == "hsl" and isinstance(state, dict):
            values = {"hsl": state}
            if type(state.get("lightness")) is int:
                values["lightness"] = state["lightness"]
            self._put(**values)
        elif field == "effects":
            effects = result.get("effects", [])
            if isinstance(effects, list):
                self._put(
                    effects=[
                        {**item, **self._preset_catalog.get(
                            item.get("slug"),
                            {"category": "pattern", "stillUrl": "", "animationUrl": ""},
                        )}
                        for item in effects
                        if isinstance(item, dict)
                        and item.get("visible") is True
                        and item.get("slug") != "color-wheel"
                    ]
                )
        elif field == "effect":
            effect = result.get("effect")
            active = result.get("effect_active")
            source = result.get("effect_source") or "unknown"
            mode = result.get("work_mode")
            mode_source = result.get("work_mode_source") or "readback"
            if type(mode) is int and 0 <= mode <= 4:
                # Current mode decides whether the reported preset is rendering.
                # A saved preset alone must not select the Effects page.
                active = mode == 1
            elif type(active) is not bool and type(result.get("last_known_work_mode")) is int:
                mode = result["last_known_work_mode"]
                if 0 <= mode <= 4:
                    active = mode == 1
                    mode_source = "saved"
                else:
                    mode = None
            elif (type(active) is not bool and self._state["pending"]
                  and (self._state["effectActive"] or self._state["colorModeActive"])):
                # A just-acknowledged activation is still known when an older
                # daemon/firmware omits mode from its follow-up response.
                active = True
                source = "cached"
                mode = None
                mode_source = "cached"
            else:
                mode = None
                mode_source = source
            if mode == 0:
                source = mode_source
            elif mode is not None:
                # Describe the weaker part of a scene/mode pair honestly.
                source = next((value for value in ("saved", "cached", "unknown")
                               if value in (source, mode_source)), "readback")
            if type(active) is bool:
                color_mode = (
                    active
                    and isinstance(effect, dict)
                    and effect.get("slug") == "color-wheel"
                )
                self._put(effect=effect if isinstance(effect, dict) else {},
                          workMode=mode, effectActive=active and not color_mode,
                          colorModeActive=color_mode,
                          previewKind=("color" if color_mode else "effect"
                                       if active and isinstance(effect, dict) and effect.get("slug")
                                       else "light" if not active and mode in (None, 0)
                                       else "unknown"),
                          previewStateSource=source)
            else:
                self._put(effect=effect if isinstance(effect, dict) else {},
                          workMode=None, effectActive=False, colorModeActive=False,
                          previewKind="unknown", previewStateSource="unknown")
        elif field == "battery":
            for item in result.get("datapoints", []):
                if isinstance(item, dict) and item.get("id") == 103 and type(item.get("value")) is int:
                    self._put(battery=item["value"])

    def _mutate(self, method: str, params: dict, field: str, action: str, *, timeout_ms: int = 20000) -> None:
        if self._state["pending"] or not self._state["connected"]:
            return
        self._put(pending=True, pendingAction=action, errorMessage="", errorDetails={})

        def done(result: dict | None, error: dict | None) -> None:
            if error is not None:
                self._error(error, action=action)
                if field == "effect":
                    self._put(effectActive=False, colorModeActive=False)
                    self._read_effect(
                        finish_pending=True,
                        preserve_error=True,
                    )
                else:
                    self._put(pending=False, pendingAction="")
                    self.refresh()
                return
            assert result is not None
            if field == "effect":
                self._put(effectActive=True, colorModeActive=False)
                self._read_effect(finish_pending=True)
            else:
                self._accept(field, result)
                if field in ("hsl", "lightness"):
                    self._put(effectActive=False, colorModeActive=False)
                self._put(pending=False, pendingAction="")
                self._refresh_field(field)

        self._enqueue(_Request(method, params, done, timeout_ms=timeout_ms))

    def _refresh_field(self, field: str) -> None:
        """Read back the changed state without queuing an unrelated full scan."""
        if not self._state["connected"]:
            return
        if field == "power":
            method, params = "control", {"command": "read"}
        elif field in ("lightness", "temperature", "hsl"):
            method, params = "lighting", {"command": field}
        else:
            return

        def done(result, error):
            if result is not None:
                self._accept(field, result)
            elif error is not None:
                self._error(error, action=f"Reading {field}")

        self._enqueue(_Request(method, params, done))

    def _read_effect(
        self,
        *,
        finish_pending: bool = False,
        preserve_error: bool = False,
    ) -> None:
        def done(result: dict | None, error: dict | None) -> None:
            if result is not None:
                self._accept("effect", result)
            elif error is not None:
                if preserve_error and self._state["errorMessage"]:
                    details = dict(self._state["errorDetails"])
                    details["readback_error"] = {
                        "code": error.get("code"),
                        "message": error.get("message"),
                    }
                    self._put(errorDetails=details)
                else:
                    self._error(error, action="Reading selected effect")
            if finish_pending:
                self._put(pending=False, pendingAction="")

        self._enqueue(_Request("effect.get", {}, done))

    @Slot(bool)
    def setPower(self, enabled: bool) -> None:
        if self._state["capabilities"].get("power"):
            self._mutate("control", {"command": "on" if enabled else "off"}, "power", "Changing power")

    @Slot(int)
    def setLightness(self, value: int) -> None:
        if self._state["capabilities"].get("lightness") and 0 <= value <= 65535:
            self._mutate("lighting", {"command": f"lightness={value}"}, "lightness", "Changing brightness")

    @Slot(int)
    def setBrightness(self, value: int) -> None:
        if not 0 <= value <= MAX_LIGHT_VALUE:
            return
        if not (self._state["effectActive"] or self._state["colorModeActive"]):
            self.setLightness(value)
            return
        if not self._state["capabilities"].get("hsl"):
            return
        hsv = mesh_hsl_to_hsv(self._state["hsl"])
        if not hsv:
            self._put(errorMessage="Changing brightness: current color is unavailable",
                      errorDetails={})
            return
        lightness, hue, saturation = hsv_to_mesh_hsl(
            value, hsv["hue"], hsv["saturation"]
        )
        self._write_hsl(lightness, hue, saturation, "Changing brightness",
                        preserve_mode=True)

    def _write_hsl(self, lightness: int, hue: int, saturation: int,
                   action: str, *, preserve_mode: bool = False) -> None:
        if self._state["pending"] or not self._state["connected"]:
            return
        self._put(pending=True, pendingAction=action,
                  errorMessage="", errorDetails={})

        def done(result: dict | None, error: dict | None) -> None:
            if error is not None:
                self._error(error, action=action)
                if preserve_mode:
                    self._put(effectActive=False, colorModeActive=False)
            else:
                assert result is not None
                self._accept("hsl", result)
                if not preserve_mode:
                    self._put(effectActive=False, colorModeActive=False)
            self._put(pending=False, pendingAction="")
            self._refresh_field("hsl")

        self._enqueue(_Request(
            "lighting", {"command": f"hsl={lightness},{hue},{saturation}"},
            done, timeout_ms=20000,
        ))

    @Slot(int)
    def setTemperature(self, value: int) -> None:
        if self._state["capabilities"].get("temperature") and 800 <= value <= 20000:
            self._mutate("lighting", {"command": f"temperature={value}"}, "temperature", "Changing temperature")

    @Slot(int, int, int)
    def setHsl(self, lightness: int, hue: int, saturation: int) -> None:
        values = (lightness, hue, saturation)
        if self._state["capabilities"].get("hsl") and all(0 <= value <= 65535 for value in values):
            self._write_hsl(lightness, hue, saturation, "Changing color")

    @Slot(int, int, int)
    def setHsv(self, value: int, hue: int, saturation: int) -> None:
        try:
            lightness, mesh_hue, mesh_saturation = hsv_to_mesh_hsl(
                value, hue, saturation
            )
        except ValueError:
            return
        if self._state["colorModeActive"]:
            self._write_hsl(lightness, mesh_hue, mesh_saturation,
                            "Changing color", preserve_mode=True)
        else:
            self.activateColor(value, hue, saturation)

    @Slot(int, int, int)
    def activateColor(self, value: int, hue: int, saturation: int) -> None:
        try:
            lightness, mesh_hue, mesh_saturation = hsv_to_mesh_hsl(
                value, hue, saturation
            )
        except ValueError:
            return
        if (not self._state["capabilities"].get("effects")
                or not self._state["capabilities"].get("hsl")
                or self._state["pending"] or not self._state["connected"]):
            return
        self._put(pending=True, pendingAction="Selecting Color",
                  errorMessage="", errorDetails={}, effectActive=False,
                  colorModeActive=False)

        def color_done(result: dict | None, error: dict | None) -> None:
            if error is not None:
                self._error(error, action="Applying color")
            else:
                assert result is not None
                self._accept("hsl", result)
            self._read_effect(finish_pending=True, preserve_error=True)
            self._refresh_field("hsl")

        def mode_done(result: dict | None, error: dict | None) -> None:
            if error is not None:
                self._error(error, action="Selecting Color")
                self._read_effect(finish_pending=True, preserve_error=True)
                self.refresh()
                return
            self._put(colorModeActive=True, pendingAction="Applying color")
            self._enqueue(_Request(
                "lighting", {"command": f"hsl={lightness},{mesh_hue},{mesh_saturation}"},
                color_done, timeout_ms=20000,
            ))

        self._enqueue(_Request("effect.activate", {"name": "color-wheel"},
                               mode_done, timeout_ms=30000))

    @Slot(str)
    def activateEffect(self, slug: str) -> None:
        if slug == "color-wheel":
            hsv = mesh_hsl_to_hsv(self._state["hsl"])
            if hsv:
                self.activateColor(hsv["value"], hsv["hue"], hsv["saturation"])
            return
        if not any(item.get("slug") == slug for item in self._state["effects"]):
            return
        if self._state["pending"] or not self._state["connected"]:
            return
        self._mutate("effect.activate", {"name": slug}, "effect", "Activating effect", timeout_ms=30000)

    def close(self) -> None:
        self._poll.stop()
        self._management_poll.stop()
        self._timeout.stop()
        self._queue.clear()
        if self._socket is not None:
            self._socket.abort()
            self._socket.deleteLater()
            self._socket = None
        self._active = None
