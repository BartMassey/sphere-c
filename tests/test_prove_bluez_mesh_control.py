"""Failure-path checks for the hardware proof, without Bluetooth hardware."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "prove_bluez_mesh_control", Path(__file__).resolve().parents[1]
    / "scripts/prove_bluez_mesh_control.py"
)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["baseline", "stage"])
async def test_failure_restores_only_after_both_baselines(monkeypatch, failure):
    state = {"devices": {
        "0x012a": {"name": "ARI", "official_config_complete": True},
        "0x0124": {"name": "BEN", "official_config_complete": True},
    }}
    values = {0x012a: False, 0x0124: True}
    writes = []

    class Store:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def load_or_create(self):
            return state

        def allocate_tid(self, state):
            return 0

    class Controller:
        def __init__(self, node, address, allocate_tid):
            self.address = address
            self.reads = 0

        async def read(self):
            self.reads += 1
            if failure == "baseline" and self.address == 0x0124 and self.reads == 1:
                raise TimeoutError("sensitive detail")
            return SimpleNamespace(present=values[self.address], target=None)

        async def set(self, desired):
            writes.append((self.address, desired))
            values[self.address] = desired
            if failure == "stage" and len(writes) == 2:
                raise TimeoutError("sensitive detail")
            return SimpleNamespace(present=desired, target=None)

    class Session:
        identity = {"unicast": 0x130}
        features = {}
        properties = {"SequenceNumber": 10}
        node = object()

        async def refresh_properties(self):
            pass

        async def close(self):
            pass

    async def open_session(state, store):
        return Session()

    monkeypatch.setattr(proof, "StateStore", Store)
    monkeypatch.setattr(proof, "BasicController", Controller)
    monkeypatch.setattr(proof, "open_bluez_mesh_session", open_session)
    result = await proof.prove(Path("unused"), seconds=1, hold_seconds=1)
    assert not result["success"]
    assert "sensitive detail" not in str(result)
    if failure == "baseline":
        assert writes == []
        assert result["restore"]["status"] == "not_needed"
    else:
        assert values == {0x012a: False, 0x0124: True}
        assert result["restore"]["status"] == "verified"
