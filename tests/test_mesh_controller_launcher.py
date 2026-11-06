"""Exercise the standalone launcher without invoking Bluetooth commands."""

import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


path = Path(__file__).resolve().parents[1] / "packaging/systemd/spherosphere-controller"
loader = importlib.machinery.SourceFileLoader("mesh_controller_launcher", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)

TARGET = "AA:BB:CC:DD:EE:FF"
USB = "00:11:22:33:44:55"


def section(index, address, settings="powered le bredr"):
    return (f"hci{index}: Primary controller\n\taddr {address} version 1\n"
            f"\tsupported settings: {settings}\n\tcurrent settings: powered\n")


@pytest.fixture
def fake_host(monkeypatch):
    host = SimpleNamespace(calls=[], output="", status=0, now=0.0, exec_args=None)

    def run(args, **kwargs):
        host.calls.append((args, kwargs))
        return SimpleNamespace(returncode=host.status if args[-1] == "info" else 0,
                               stdout=host.output)

    def execv(*args):
        host.exec_args = args

    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(launcher.os, "execv", execv)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: host.now)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: setattr(host, "now", host.now + seconds))
    return host


@pytest.mark.parametrize("target_index,usb_index", [(7, 0), (0, 7)])
def test_resolves_address_and_executes_exact_command(fake_host, target_index, usb_index):
    fake_host.output = section(usb_index, USB) + section(target_index, TARGET.lower())
    launcher.launch(TARGET)
    assert [call[0] for call in fake_host.calls] == [
        ["/usr/bin/btmgmt", "info"],
        ["/usr/bin/btmgmt", "--index", str(target_index), "power", "off"],
    ]
    assert all(call[1]["timeout"] <= 2 and call[1]["capture_output"]
               for call in fake_host.calls)
    assert fake_host.exec_args == ("/usr/libexec/bluetooth/bluetooth-meshd", [
        "/usr/libexec/bluetooth/bluetooth-meshd", "--io", f"generic:hci{target_index}"])


def test_invalid_address_runs_no_command(fake_host, monkeypatch):
    monkeypatch.setattr("sys.argv", ["launcher", "--controller", "invalid"])
    with pytest.raises(SystemExit):
        launcher.main()
    assert fake_host.calls == []
    assert fake_host.exec_args is None


@pytest.mark.parametrize("status", [0, 1])
def test_unavailable_never_powers_another_controller(fake_host, status):
    fake_host.output = section(0, USB)
    fake_host.status = status
    with pytest.raises(RuntimeError, match="unavailable"):
        launcher.launch(TARGET)
    assert fake_host.now == 15
    assert all(call[0] == ["/usr/bin/btmgmt", "info"] for call in fake_host.calls)
    assert fake_host.exec_args is None


def test_requires_le_support(fake_host):
    fake_host.output = section(0, USB) + section(7, TARGET, "powered bredr")
    with pytest.raises(RuntimeError, match="LE support"):
        launcher.launch(TARGET)
    assert len(fake_host.calls) == 1
    assert fake_host.exec_args is None


def test_ambiguous_address_fails_closed(fake_host):
    fake_host.output = section(0, TARGET) + section(7, TARGET)
    with pytest.raises(RuntimeError, match="ambiguous"):
        launcher.launch(TARGET)
    assert len(fake_host.calls) == 1
    assert fake_host.exec_args is None
