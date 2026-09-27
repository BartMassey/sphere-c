import json
import os
import stat

import pytest

from sphere_mesh.state import (
    SCHEMA_VERSION,
    StateLockedError,
    StateError,
    StateStore,
    load_or_create_state,
    new_state,
    save_state,
)


def test_legacy_state_migration_selects_latest_device(tmp_path):
    path = tmp_path / "state.json"
    legacy = new_state()
    legacy.pop("schema_version")
    legacy.pop("tid")
    legacy.pop("active_device")
    legacy["devices"] = {
        "0x0112": {"uuid": "00" * 16},
        "0x0118": {"uuid": "00" * 16},
    }
    path.write_text(json.dumps(legacy))

    state = load_or_create_state(path)

    assert state["schema_version"] == SCHEMA_VERSION
    assert state["tid"] == 0
    assert state["active_device"] == "0x0118"
    assert json.loads(path.read_text())["active_device"] == "0x0118"


def test_save_state_syncs_file_and_directory(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    calls = []
    real_fsync = os.fsync

    def record_fsync(fd):
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", record_fsync)
    save_state(path, new_state())

    assert len(calls) == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_state_store_requires_exclusive_owner(tmp_path):
    path = tmp_path / "state.json"
    first = StateStore(path)
    second = StateStore(path)
    first.acquire()
    try:
        with pytest.raises(StateLockedError, match="another process"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_state_store_requires_lock_for_io(tmp_path):
    store = StateStore(tmp_path / "state.json")

    with pytest.raises(StateLockedError, match="locked before loading"):
        store.load_or_create()
    with pytest.raises(StateLockedError, match="locked before saving"):
        store.save(new_state())


def test_state_store_advances_sequence_monotonically(tmp_path):
    store = StateStore(tmp_path / "state.json")
    with store:
        state = store.load_or_create()
        store.advance_sequence(state, 42)
        store.advance_sequence(state, 42)
        with pytest.raises(StateError, match="cannot move backward"):
            store.advance_sequence(state, 41)

    assert json.loads(store.path.read_text())["seq"] == 42


def test_state_store_allocates_tid_before_return(tmp_path):
    store = StateStore(tmp_path / "state.json")
    with store:
        state = store.load_or_create()
        state["tid"] = 0xFF
        allocated = store.allocate_tid(state)

    persisted = json.loads(store.path.read_text())
    assert allocated == 0xFF
    assert persisted["tid"] == 0
