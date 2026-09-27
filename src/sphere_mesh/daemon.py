"""Persistent connection supervision for the Sphere mesh controller."""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from btmesh.access import AccessError
from btmesh.bearer import BearerError
from btmesh.crypto import k3

from .api import ApiRequestError, UnixApiServer
from .control import BasicController, ControlUnavailable
from .lighting import (
    LightingController,
    LightingUnavailable,
    LightingUnsupported,
    parse_lighting_command,
)
from .state import StateError, StateLockedError, StateStore
from .transport import (
    LocalTransport,
    find_proxy_node,
    open_mesh_proxy_session,
)
from .vendor import (
    TuyaVendorController,
    VendorUnavailable,
    VendorUnsupported,
    effect_catalog,
    effect_name,
    effect_selection,
    json_value,
)


logger = logging.getLogger("sphere-daemon")
DEFAULT_STATE = Path("state/sphere_state.json")
DEFAULT_SOCKET = Path("state/sphere.sock")
PROXY_SCAN_SECONDS = 30.0


class SupervisedSession(Protocol):
    async def wait_lost(self) -> BaseException: ...

    async def close(self) -> None: ...


class ConnectionSupervisor:
    def __init__(
        self,
        connect: Callable[[], Awaitable[SupervisedSession]],
        *,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        if backoff_initial < 0 or backoff_max < backoff_initial:
            raise ValueError("invalid reconnect backoff")
        self._connect = connect
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Condition()
        self._stopping = asyncio.Event()
        self._session: SupervisedSession | None = None
        self._generation = 0
        self.last_failure: BaseException | None = None

    @property
    def session(self) -> SupervisedSession | None:
        return self._session

    @property
    def generation(self) -> int:
        return self._generation

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("connection supervisor is already running")
        if self._stopping.is_set():
            raise RuntimeError("connection supervisor has been stopped")
        self._task = asyncio.create_task(self._run())

    async def wait_ready(
        self, *, after_generation: int = 0
    ) -> tuple[int, SupervisedSession]:
        async with self._changed:
            await self._changed.wait_for(
                lambda: (
                    self._session is not None
                    and self._generation > after_generation
                )
                or self._stopping.is_set()
                or (self._task is not None and self._task.done())
            )
            if self._session is None:
                if self._task is None or self._task.cancelled():
                    raise RuntimeError("connection supervisor stopped")
                failure = self._task.exception()
                if failure is not None:
                    raise failure
                raise RuntimeError("connection supervisor stopped")
            return self._generation, self._session

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None
        async with self._changed:
            self._changed.notify_all()

    async def _publish(self, session: SupervisedSession | None) -> None:
        async with self._changed:
            self._session = session
            if session is not None:
                self._generation += 1
            self._changed.notify_all()

    async def _pause(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def _run(self) -> None:
        delay = self._backoff_initial
        try:
            while not self._stopping.is_set():
                try:
                    session = await self._connect()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_failure = exc
                    logger.warning("connection attempt failed: %s", exc)
                    await self._pause(delay)
                    delay = min(self._backoff_max, max(delay * 2, 0.001))
                    continue
                delay = self._backoff_initial
                await self._publish(session)
                try:
                    self.last_failure = await session.wait_lost()
                    logger.warning("connection lost: %s", self.last_failure)
                finally:
                    await self._publish(None)
                    await session.close()
                if not self._stopping.is_set():
                    await self._pause(delay)
        finally:
            session = self._session
            await self._publish(None)
            if session is not None:
                await session.close()


class SphereRuntime:
    def __init__(
        self,
        supervisor: ConnectionSupervisor,
        state: dict,
        store: StateStore,
        unicast: int,
    ) -> None:
        self._supervisor = supervisor
        self._state = state
        self._store = store
        self._unicast = unicast
        self._command_lock = asyncio.Lock()

    async def handle(self, method: str, params: dict) -> dict:
        if method == "health":
            if params:
                raise ApiRequestError(
                    "invalid_params", "health does not accept parameters"
                )
            return self.health()
        if method == "effect.list":
            if params:
                raise ApiRequestError(
                    "invalid_params", "effect.list does not accept parameters"
                )
            return {"effects": effect_catalog()}
        if method not in (
            "control", "lighting", "vendor.read", "effect.get",
            "effect.set", "effect.select", "effect.activate",
        ):
            raise ApiRequestError("unknown_method", f"unknown method: {method}")
        async with self._command_lock:
            session = self._supervisor.session
            if session is None:
                raise ApiRequestError(
                    "unavailable", "Sphere Bluetooth session is not connected"
                )
            if method == "control":
                return await self._control(session, params)
            if method == "lighting":
                return await self._lighting(session, params)
            if method == "vendor.read":
                return await self._vendor_read(session, params)
            return await self._effect(session, method, params)

    def health(self) -> dict:
        failure = self._supervisor.last_failure
        return {
            "status": (
                "connected" if self._supervisor.session is not None else "connecting"
            ),
            "connected": self._supervisor.session is not None,
            "generation": self._supervisor.generation,
            "device": f"0x{self._unicast:04x}",
            "last_failure": None if failure is None else str(failure),
        }

    async def _control(self, session, params: dict) -> dict:
        command = params.get("command")
        if set(params) != {"command"} or command not in (
            "read",
            "on",
            "off",
            "toggle",
        ):
            raise ApiRequestError(
                "invalid_params", "control command must be read, on, off, or toggle"
            )
        controller = BasicController(
            session.node,
            self._unicast,
            lambda: self._store.allocate_tid(self._state),
        )
        try:
            result = await controller.execute(command)
        except ControlUnavailable as exc:
            raise ApiRequestError("unavailable", str(exc)) from exc
        return {
            "command": command,
            "state": {
                "present": result.present,
                "target": result.target,
                "remaining_time": result.remaining_time,
                "effective": result.effective,
            },
        }

    async def _lighting(self, session, params: dict) -> dict:
        raw_command = params.get("command")
        if set(params) != {"command"} or not isinstance(raw_command, str):
            raise ApiRequestError(
                "invalid_params", "lighting requires one string command"
            )
        try:
            command = parse_lighting_command(raw_command)
        except ValueError as exc:
            raise ApiRequestError("invalid_params", str(exc)) from exc
        controller = LightingController(
            session.node,
            self._state.get("composition", {}),
            lambda: self._store.allocate_tid(self._state),
        )
        try:
            result = await controller.execute(command)
        except LightingUnsupported as exc:
            raise ApiRequestError("unsupported", str(exc)) from exc
        except LightingUnavailable as exc:
            raise ApiRequestError("unavailable", str(exc)) from exc
        return {
            "command": raw_command,
            "model": command.model,
            "state": asdict(result),
        }

    async def _vendor_read(self, session, params: dict) -> dict:
        dp_ids = params.get("dp_ids")
        if (
            set(params) != {"dp_ids"}
            or not isinstance(dp_ids, list)
            or not dp_ids
            or len(dp_ids) > 0xFF
            or any(
                not isinstance(dp_id, int)
                or isinstance(dp_id, bool)
                or not 0 <= dp_id <= 0xFF
                for dp_id in dp_ids
            )
            or len(set(dp_ids)) != len(dp_ids)
        ):
            raise ApiRequestError(
                "invalid_params",
                "vendor.read requires 1..255 unique uint8 dp_ids",
            )
        controller = TuyaVendorController(
            session.node,
            self._state.get("composition", {}),
        )
        try:
            data = await controller.read(dp_ids)
        except VendorUnsupported as exc:
            raise ApiRequestError("unsupported", str(exc)) from exc
        except VendorUnavailable as exc:
            raise ApiRequestError("unavailable", str(exc)) from exc
        except AccessError as exc:
            raise ApiRequestError("protocol_error", str(exc)) from exc
        return self._vendor_snapshot(data, dp_ids)

    @staticmethod
    def _vendor_snapshot(data, dp_ids: list[int]) -> dict:
        returned_ids = {record.dp_id for record in data.records}
        return {
            "command_type": data.command_type,
            "requested_dp_ids": dp_ids,
            "missing_dp_ids": [
                dp_id for dp_id in dp_ids if dp_id not in returned_ids
            ],
            "datapoints": [
                {
                    "id": record.dp_id,
                    "type": record.dp_type.name.lower(),
                    "value": json_value(record),
                    "raw": record.raw.hex(),
                }
                for record in data.records
            ],
        }

    async def _effect(self, session, method: str, params: dict) -> dict:
        if method == "effect.get":
            if params:
                raise ApiRequestError(
                    "invalid_params", "effect.get does not accept parameters"
                )
        else:
            if set(params) != {"name"}:
                raise ApiRequestError(
                    "invalid_params", f"{method} requires one name"
                )
            try:
                values = effect_selection(params["name"])
            except ValueError as exc:
                raise ApiRequestError("invalid_params", str(exc)) from exc
        controller = TuyaVendorController(
            session.node, self._state.get("composition", {})
        )
        try:
            if method == "effect.get":
                data = await controller.read([2, 102])
            elif method == "effect.activate":
                mode_data = await controller.write({2: values[2]})
                data = await controller.write({102: values[102]})
            else:
                data = await controller.write({102: values[102]})
        except VendorUnsupported as exc:
            raise ApiRequestError("unsupported", str(exc)) from exc
        except VendorUnavailable as exc:
            raise ApiRequestError("unavailable", str(exc)) from exc
        except AccessError as exc:
            raise ApiRequestError("protocol_error", str(exc)) from exc
        snapshot = self._vendor_snapshot(data, [2, 102])
        effect = None
        for record in reversed(data.records):
            if record.dp_id == 102:
                if record.dp_type.name != "ENUM" or not 0 <= record.value <= 31:
                    raise ApiRequestError(
                        "protocol_error", "invalid DP 102 effect response"
                    )
                value = record.value
                effect = effect_catalog()[value]
                break
        if method == "effect.get":
            return {"effect": effect, "snapshot": snapshot}
        result = {
            "requested_effect": effect_name(values[102]),
            "acknowledged": True,
            "reported_effect": effect,
            "response": self._vendor_snapshot(data, [102]),
        }
        if method == "effect.activate":
            result["mode_response"] = self._vendor_snapshot(mode_data, [2])
        return result


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, separators=(",", ":"))


def setup_logging(verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.handlers[:] = [handler]
    for noisy in ("bleak", "dbus_fast", "habluetooth"):
        logging.getLogger(noisy).setLevel(
            logging.INFO if verbose else logging.WARNING
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Sphere service")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _active_unicast(state: dict) -> int:
    active = state.get("active_device")
    if active is None or active not in state["devices"]:
        raise StateError("state has no active provisioned device")
    return int(active, 16)


async def run_service(args: argparse.Namespace) -> int:
    store = StateStore(args.state)
    transport = LocalTransport()
    server: UnixApiServer | None = None
    supervisor: ConnectionSupervisor | None = None
    try:
        store.acquire()
        state = store.load_or_create()
        unicast = _active_unicast(state)
        entry = state["devices"][f"0x{unicast:04x}"]
        netkey = bytes.fromhex(state["netkey"])
        appkey = bytes.fromhex(state["appkey"])
        device_key = bytes.fromhex(entry["device_key"])
        await transport.start()

        async def connect():
            scanner = transport.scanner()
            try:
                match, _ = await find_proxy_node(
                    scanner,
                    k3(netkey),
                    timeout=PROXY_SCAN_SECONDS,
                    stop_after=False,
                    identity_netkey=netkey,
                    node_addresses=(unicast,),
                )
                if match is None:
                    raise BearerError("no authenticated Sphere proxy advert seen")
                return await open_mesh_proxy_session(
                    transport,
                    match.device,
                    netkey=netkey,
                    appkey=appkey,
                    device_key=device_key,
                    iv_index=state["iv_index"],
                    provisioner_addr=state["provisioner_addr"],
                    unicast=unicast,
                    seq=state["seq"],
                    persist_seq=lambda value: store.advance_sequence(state, value),
                )
            finally:
                await scanner.stop()

        supervisor = ConnectionSupervisor(connect)
        runtime = SphereRuntime(supervisor, state, store, unicast)
        server = UnixApiServer(args.socket, runtime.handle)
        await server.start()
        supervisor.start()
        logger.info("Sphere service listening on %s", args.socket)

        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stopping.set)
        await stopping.wait()
        logger.info("Sphere service stopping")
        return 0
    except (StateError, StateLockedError, OSError, RuntimeError) as exc:
        logger.error("service startup failed: %s", exc)
        return 1
    finally:
        if server is not None:
            await server.close()
        if supervisor is not None:
            await supervisor.stop()
        await transport.stop()
        if store.locked:
            store.release()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return asyncio.run(run_service(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
