"""Qt state model and local socket client for the Sphere dashboard."""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalSocket
except ImportError as exc:
    raise RuntimeError(
        "The desktop app requires PySide6; install sphere-mesh-lab[gui]"
    ) from exc


MAX_RESPONSE_BYTES = 64 * 1024
MAX_LIGHT_VALUE = 0xFFFF


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


class SphereBackend(QObject):
    """QML-facing service model; all I/O stays on the Qt event loop."""

    stateChanged = Signal()

    def __init__(
        self,
        socket_path: str | Path = "state/sphere.sock",
        parent: QObject | None = None,
        *,
        start: bool = True,
        poll_connected_ms: int = 10000,
        poll_waiting_ms: int = 2000,
    ) -> None:
        super().__init__(parent)
        self._socket_path = str(Path(socket_path).expanduser().resolve())
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
            "effects": [],
            "battery": None,
            "generation": 0,
            "device": "",
            "lastFailure": "",
        }
        self._verification: dict = {}
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._poll_status)
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
    effects = Property("QVariant", lambda self: self._get("effects"), notify=stateChanged)
    battery = Property("QVariant", lambda self: self._get("battery"), notify=stateChanged)
    generation = Property(int, lambda self: self._get("generation"), notify=stateChanged)
    device = Property(str, lambda self: self._get("device"), notify=stateChanged)
    lastFailure = Property(str, lambda self: self._get("lastFailure"), notify=stateChanged)
    socketPath = Property(str, lambda self: self._socket_path, constant=True)
    diagnostics = Property(str, lambda self: self._diagnostics(), notify=stateChanged)

    def _put(self, **values) -> None:
        changed = False
        for key, value in values.items():
            if self._state[key] != value:
                self._state[key] = value
                changed = True
        if changed:
            self.stateChanged.emit()

    def _diagnostics(self) -> str:
        data = {
            "socket": self._socket_path,
            "connection": self._state["connectionState"],
            "generation": self._state["generation"],
            "device": self._state["device"],
            "last_failure": self._state["lastFailure"],
            "capabilities": self._state["capabilities"],
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
            self._finish(None, {"code": "protocol_error", "message": "Service response is too large"})
            return
        if b"\n" not in self._buffer:
            return
        raw, _, extra = self._buffer.partition(b"\n")
        if extra:
            self._finish(None, {"code": "protocol_error", "message": "Service sent more than one response"})
            return
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            self._finish(None, {"code": "protocol_error", "message": "Service returned invalid JSON"})
            return
        if (
            not isinstance(response, dict)
            or response.get("version") != 1
            or response.get("id") != self._active_id
            or type(response.get("ok")) is not bool
        ):
            self._finish(None, {"code": "protocol_error", "message": "Service returned an invalid response"})
            return
        if response["ok"]:
            result = response.get("result")
            if not isinstance(result, dict):
                self._finish(None, {"code": "protocol_error", "message": "Service returned an invalid result"})
            else:
                self._finish(result, None)
        else:
            error = response.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                error = {"code": "protocol_error", "message": "Service returned an invalid error"}
            self._finish(None, error)

    def _on_socket_error(self, _error) -> None:
        socket = self._socket
        if socket is not None and self._active is not None:
            self._finish(None, {"code": "transport_error", "message": socket.errorString()})

    def _on_timeout(self) -> None:
        if self._active is not None:
            self._finish(None, {"code": "timeout", "message": "Service did not respond in time; the result is uncertain"})

    def _finish(self, result: dict | None, error: dict | None) -> None:
        request, self._active = self._active, None
        socket, self._socket = self._socket, None
        self._timeout.stop()
        if socket is not None:
            socket.abort()
            socket.deleteLater()
        if request is None:
            return
        request.callback(result, error)
        QTimer.singleShot(0, self._pump)

    def _poll_status(self) -> None:
        self._poll.stop()
        if self._status_due:
            return
        self._status_due = True
        self._enqueue(_Request("status", {}, self._status_result, timeout_ms=4000))

    def _status_result(self, result: dict | None, error: dict | None) -> None:
        self._status_due = False
        if error is not None:
            self._put(connectionState="unavailable", connected=False,
                      effectActive=False, colorModeActive=False)
            if self._state["pending"]:
                self._error(error, action=self._state["pendingAction"])
            self._schedule_poll()
            return
        assert result is not None
        connected = result.get("connected") is True
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
            self._put(effectActive=False, colorModeActive=False)
        self._socket_identity = socket_identity
        was_connected = self._state["connected"]
        self._put(
            connectionState="connected" if connected else "reconnecting",
            connected=connected,
            generation=generation,
            device=str(result.get("device") or ""),
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
        for model in ("lightness", "hsl"):
            if capabilities.get(model):
                operations.append(("lighting", {"command": model}, model))
        if capabilities.get("effects"):
            operations.extend((("effect.list", {}, "effects"), ("effect.get", {}, "effect")))
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
            self._put(hsl=state)
        elif field == "effects":
            effects = result.get("effects", [])
            if isinstance(effects, list):
                self._put(
                    effects=[
                        item
                        for item in effects
                        if isinstance(item, dict)
                        and item.get("visible") is True
                        and item.get("slug") != "color-wheel"
                    ]
                )
        elif field == "effect":
            effect = result.get("effect")
            self._put(effect=effect if isinstance(effect, dict) else {})
            active = result.get("effect_active")
            if type(active) is bool:
                color_mode = (
                    active
                    and isinstance(effect, dict)
                    and effect.get("slug") == "color-wheel"
                )
                self._put(effectActive=active and not color_mode,
                          colorModeActive=color_mode)
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
                self.refresh()

        self._enqueue(_Request(method, params, done, timeout_ms=timeout_ms))

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
            self.refresh()

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
            self.refresh()

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
        self._timeout.stop()
        self._queue.clear()
        if self._socket is not None:
            self._socket.abort()
            self._socket.deleteLater()
            self._socket = None
        self._active = None
