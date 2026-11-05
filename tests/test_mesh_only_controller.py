"""Exercise the ownership handoff using only fake commands and files."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "mesh_only_controller", Path(__file__).resolve().parents[1]
    / "scripts/mesh_only_controller.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    state = tmp_path / "state" / "handoff.json"
    override = tmp_path / "run" / "bluetooth-mesh.service.d" / "test.conf"
    monkeypatch.setattr(module, "STATE", state)
    monkeypatch.setattr(module, "OVERRIDE", override)
    monkeypatch.delenv("SUDO_UID", raising=False)
    calls = []
    indices = {"motherboard": 7, "usb": 0}
    powered = {"motherboard": True, "usb": True}

    def run(*args, check=True):
        calls.append(args)
        if args == ("btmgmt", "info"):
            sections = []
            for name, address in (("usb", "00:11:22:33:44:55"),
                                  ("motherboard", module.ADDRESS)):
                settings = "powered connectable" if powered[name] else "connectable"
                sections.append(f"hci{indices[name]}: Primary controller\n"
                                f"\taddr {address} version 1\n"
                                f"\tcurrent settings: {settings}\n")
            return SimpleNamespace(stdout="".join(sections), returncode=0)
        if args[:1] == ("btmgmt",) and args[-2] == "power":
            index = int(args[2])
            name = next(name for name, value in indices.items() if value == index)
            powered[name] = args[-1] == "on"
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(module, "run", run)
    return SimpleNamespace(state=state, override=override, calls=calls,
                           indices=indices, powered=powered)


def invoke(monkeypatch, action, *args):
    monkeypatch.setattr("sys.argv", ["mesh_only_controller.py", action, *args])
    module.main()


@pytest.mark.parametrize("was_powered", [True, False])
def test_handoff_tracks_motherboard_address_and_restores_power(handoff, monkeypatch, was_powered):
    handoff.powered["motherboard"] = was_powered
    invoke(monkeypatch, "start")
    assert handoff.override.read_text().endswith("--io generic:hci7\n")
    assert not handoff.powered["motherboard"]
    assert handoff.powered["usb"]
    assert json.loads(handoff.state.read_text())["active"] is True
    assert ("btmgmt", "--index", "7", "power", "off") in handoff.calls

    # Indices may change while the controller is held by meshd.
    handoff.indices.update(motherboard=2, usb=7)
    invoke(monkeypatch, "restore")
    assert handoff.powered == {"motherboard": was_powered, "usb": True}
    assert not handoff.override.exists()
    assert json.loads(handoff.state.read_text())["active"] is False
    assert ("btmgmt", "--index", "2", "power", "on" if was_powered else "off") in handoff.calls
    assert not any("bluetooth.service" in call for call in handoff.calls)
    assert set(handoff.state.parent.iterdir()) == {handoff.state}
    assert list(handoff.override.parent.iterdir()) == []


def test_existing_override_is_never_changed(handoff, monkeypatch):
    handoff.override.parent.mkdir(parents=True)
    handoff.override.write_text("existing contents\n")
    with pytest.raises(RuntimeError):
        invoke(monkeypatch, "start")
    assert handoff.override.read_text() == "existing contents\n"
    assert not handoff.state.exists()
    assert handoff.calls == []


def test_selected_controller_restores_by_address_after_reindex(handoff, monkeypatch):
    invoke(monkeypatch, "start", "--controller", "00:11:22:33:44:55")
    assert json.loads(handoff.state.read_text())["address"] == "00:11:22:33:44:55"
    assert handoff.powered == {"motherboard": True, "usb": False}
    handoff.indices.update(motherboard=0, usb=4)
    invoke(monkeypatch, "restore")
    assert handoff.powered == {"motherboard": True, "usb": True}
    assert ("btmgmt", "--index", "4", "power", "on") in handoff.calls


@pytest.mark.parametrize("args", [
    ("start", "--controller", "invalid"),
    ("restore", "--controller", "00:11:22:33:44:55"),
])
def test_invalid_controller_arguments_do_not_run_commands(handoff, monkeypatch, args):
    with pytest.raises(SystemExit):
        invoke(monkeypatch, *args)
    assert handoff.calls == []


def test_missing_controller_does_not_change_host(handoff, monkeypatch):
    with pytest.raises(RuntimeError, match="exactly one controller"):
        invoke(monkeypatch, "start", "--controller", "AA:BB:CC:DD:EE:FF")
    assert handoff.calls == [("btmgmt", "info")]
    assert not handoff.state.exists()


def test_restore_refuses_changed_override(handoff, monkeypatch):
    invoke(monkeypatch, "start")
    handoff.override.write_text("changed\n")
    handoff.calls.clear()
    with pytest.raises(RuntimeError, match="override changed"):
        invoke(monkeypatch, "restore")
    assert handoff.override.read_text() == "changed\n"
    assert handoff.calls == []
