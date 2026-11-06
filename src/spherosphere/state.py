"""Persistent controller state and exclusive process ownership."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from copy import deepcopy
from pathlib import Path


SCHEMA_VERSION = 1
MAX_NEXT_SEQUENCE = 0x1000000


class StateError(ValueError):
    """Controller state is invalid or cannot be used safely."""


class StateLockedError(StateError):
    """Another process owns this controller state."""


def new_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "netkey": secrets.token_bytes(16).hex(),
        "appkey": secrets.token_bytes(16).hex(),
        "iv_index": 0,
        "netkey_index": 0,
        "appkey_index": 0,
        "provisioner_addr": 0x0001,
        "next_unicast": 0x0100,
        "seq": 0,
        "tid": 0,
        "active_device": None,
        "devices": {},
    }


def migrate_state(value: dict) -> tuple[dict, bool]:
    if not isinstance(value, dict):
        raise StateError("state root is not an object")
    state = deepcopy(value)
    changed = False
    version = state.get("schema_version")
    if version is None:
        state["schema_version"] = SCHEMA_VERSION
        state.setdefault("tid", 0)
        devices = state.get("devices")
        if isinstance(devices, dict) and devices:
            state.setdefault("active_device", next(reversed(devices)))
        else:
            state.setdefault("active_device", None)
        changed = True
    elif version != SCHEMA_VERSION:
        raise StateError(
            f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}"
        )
    validate_state(state)
    return state, changed


def validate_state(state: dict) -> None:
    field_types = {
        "schema_version": int,
        "netkey": str,
        "appkey": str,
        "iv_index": int,
        "netkey_index": int,
        "appkey_index": int,
        "provisioner_addr": int,
        "next_unicast": int,
        "seq": int,
        "tid": int,
        "devices": dict,
    }
    for field, expected in field_types.items():
        if not isinstance(state.get(field), expected):
            raise StateError(
                f"field {field!r} is missing or not a {expected.__name__}"
            )
    for key_field in ("netkey", "appkey"):
        try:
            key = bytes.fromhex(state[key_field])
        except ValueError as exc:
            raise StateError(f"field {key_field!r} is not hex") from exc
        if len(key) != 16:
            raise StateError(f"field {key_field!r} is not 16 bytes of hex")
    if not 0 <= state["seq"] <= MAX_NEXT_SEQUENCE:
        raise StateError("field 'seq' is outside the 24-bit cursor range")
    if not 0 <= state["tid"] <= 0xFF:
        raise StateError("field 'tid' is outside the 8-bit range")
    active = state.get("active_device")
    if active is not None:
        if not isinstance(active, str):
            raise StateError("field 'active_device' is not a string or null")
        if active not in state["devices"]:
            raise StateError("field 'active_device' is not present in devices")


def save_state(path: Path, state: dict) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(fd)


def load_or_create_state(path: Path) -> dict:
    if path.exists():
        try:
            state, changed = migrate_state(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError, StateError) as exc:
            raise StateError(f"state file {path} is unreadable: {exc}") from exc
        if changed:
            save_state(path, state)
        return state
    state = new_state()
    save_state(path, state)
    return state


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_fd: int | None = None

    @property
    def locked(self) -> bool:
        return self._lock_fd is not None

    def acquire(self) -> None:
        if self.locked:
            raise StateLockedError(f"state store {self.path} is already locked")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise StateLockedError(
                f"state store {self.path} is in use by another process"
            ) from exc
        self._lock_fd = fd

    def release(self) -> None:
        if self._lock_fd is None:
            return
        fd = self._lock_fd
        self._lock_fd = None
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def load_or_create(self) -> dict:
        if not self.locked:
            raise StateLockedError("state store must be locked before loading")
        return load_or_create_state(self.path)

    def save(self, state: dict) -> None:
        if not self.locked:
            raise StateLockedError("state store must be locked before saving")
        save_state(self.path, state)

    def advance_sequence(self, state: dict, next_sequence: int) -> None:
        current = state["seq"]
        if next_sequence < current:
            raise StateError(
                f"sequence cursor cannot move backward from {current} "
                f"to {next_sequence}"
            )
        if next_sequence > MAX_NEXT_SEQUENCE:
            raise StateError("sequence cursor exceeds the 24-bit space")
        if next_sequence == current:
            return
        state["seq"] = next_sequence
        self.save(state)

    def allocate_tid(self, state: dict) -> int:
        tid = state["tid"]
        state["tid"] = (tid + 1) & 0xFF
        self.save(state)
        return tid

    def __enter__(self) -> StateStore:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
