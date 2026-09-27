"""Versioned local JSON API over a private Unix-domain socket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


API_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
logger = logging.getLogger("sphere-api")


class ApiRequestError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class ApiProtocolError(RuntimeError):
    pass


ApiHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _response(
    request_id: object,
    *,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "version": API_VERSION,
        "id": request_id,
        "ok": error is None,
    }
    if error is None:
        response["result"] = result or {}
    else:
        response["error"] = error
    return response


def _parse_request(raw: bytes) -> tuple[object, str, dict[str, Any]]:
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiRequestError("invalid_json", "request is not valid JSON") from exc
    if not isinstance(request, dict):
        raise ApiRequestError("invalid_request", "request must be a JSON object")
    request_id = request.get("id")
    if request.get("version") != API_VERSION:
        raise ApiRequestError(
            "unsupported_version",
            f"API version {request.get('version')!r} is unsupported",
        )
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise ApiRequestError("invalid_request", "method must be a nonempty string")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ApiRequestError("invalid_request", "params must be an object")
    return request_id, method, params


class UnixApiServer:
    def __init__(self, path: Path, handler: ApiHandler) -> None:
        self.path = path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None
        self._socket_identity: tuple[int, int] | None = None

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("API server is already running")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.path.is_symlink():
            mode = self.path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to replace non-socket {self.path}")
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.path), timeout=0.5
                )
            except (ConnectionRefusedError, FileNotFoundError):
                self.path.unlink(missing_ok=True)
            except TimeoutError as exc:
                raise RuntimeError(
                    f"API socket at {self.path} did not accept a probe"
                ) from exc
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"API socket is already active at {self.path}")
        self._server = await asyncio.start_unix_server(
            self._serve_client,
            path=self.path,
            limit=MAX_MESSAGE_BYTES + 1,
        )
        os.chmod(self.path, 0o600)
        socket_stat = self.path.stat()
        self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        try:
            socket_stat = self.path.lstat()
        except FileNotFoundError:
            return
        identity = (socket_stat.st_dev, socket_stat.st_ino)
        if stat.S_ISSOCK(socket_stat.st_mode) and identity == self._socket_identity:
            self.path.unlink()

    async def _serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_id: object = None
        try:
            try:
                raw = await reader.readline()
            except ValueError as exc:
                raise ApiRequestError(
                    "message_too_large",
                    f"request exceeds {MAX_MESSAGE_BYTES} bytes",
                ) from exc
            if not raw:
                return
            if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise ApiRequestError(
                    "message_too_large",
                    f"request exceeds {MAX_MESSAGE_BYTES} bytes",
                )
            request_id, method, params = _parse_request(raw)
            result = await self._handler(method, params)
            response = _response(request_id, result=result)
        except ApiRequestError as exc:
            error = {"code": exc.code, "message": str(exc)}
            if exc.details is not None:
                error["details"] = exc.details
            response = _response(
                request_id,
                error=error,
            )
        except Exception:
            logger.exception("API handler failed")
            response = _response(
                request_id,
                error={
                    "code": "internal_error",
                    "message": "internal service error",
                },
            )
        writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def request(
    path: Path,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: object = 1,
) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(path)
    payload = {
        "version": API_VERSION,
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    writer.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
    try:
        await writer.drain()
        raw = await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw:
        raise ApiProtocolError("service closed without a response")
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProtocolError("service returned invalid JSON") from exc
    if (
        not isinstance(response, dict)
        or response.get("version") != API_VERSION
        or response.get("id") != request_id
        or not isinstance(response.get("ok"), bool)
    ):
        raise ApiProtocolError("service returned an invalid response envelope")
    return response
