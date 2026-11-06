"""Test installer actions with temporary paths and simulated commands."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "install_mesh_controller", Path(__file__).resolve().parents[1]
    / "scripts/install_mesh_controller.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def host(tmp_path, monkeypatch):
    for name in ("SOURCE", "LAUNCHER", "DROPIN", "LEGACY"):
        monkeypatch.setattr(module, name, tmp_path / name / "file.conf")
    module.SOURCE.parent.mkdir()
    module.SOURCE.write_bytes(b"launcher\n")
    state = SimpleNamespace(calls=[], output="", now=0.0)

    def run(args, **kwargs):
        state.calls.append((args, kwargs))
        return SimpleNamespace(stdout=state.output)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: setattr(state, "now", state.now + seconds))
    return state


def test_install_manages_files_and_service(host):
    module.LEGACY.parent.mkdir()
    module.LEGACY.write_text("[Service]\nExecStart=\nExecStart=/usr/libexec/bluetooth/bluetooth-meshd --io generic:hci3\n")
    module.install(ADDRESS)
    assert module.LAUNCHER.read_bytes() == b"launcher\n"
    assert module.LAUNCHER.stat().st_mode & 0o777 == 0o755
    assert module.DROPIN.read_text() == module.contents(ADDRESS)
    assert not module.LEGACY.exists()
    assert module.LEGACY.with_suffix(".conf.previous").exists()
    assert [c[0] for c in host.calls] == [
        (module.SYSTEMCTL, "daemon-reload"),
        (module.SYSTEMCTL, "enable", module.SERVICE),
        (module.SYSTEMCTL, "restart", module.SERVICE),
        (module.SYSTEMCTL, "is-active", "--quiet", module.SERVICE),
        (module.SYSTEMCTL, "is-active", "--quiet", module.SERVICE),
    ]
    assert all(c[1]["check"] and c[1]["timeout"] <= 30 for c in host.calls)


def test_existing_managed_dropin_is_saved(host):
    module.DROPIN.parent.mkdir()
    module.DROPIN.write_text(module.contents(ADDRESS))
    module.install("11:22:33:44:55:66")
    assert module.DROPIN.with_suffix(".conf.previous").read_text() == module.contents(ADDRESS)
    assert module.DROPIN.read_text() == module.contents("11:22:33:44:55:66")


@pytest.mark.parametrize("target", ["DROPIN", "LEGACY", "LAUNCHER"])
def test_rejects_unmanaged_or_symlink_before_changes(host, target):
    path = getattr(module, target)
    path.parent.mkdir()
    if target == "LAUNCHER":
        path.symlink_to(module.SOURCE)
    else:
        path.write_text("unmanaged\n")
    with pytest.raises(RuntimeError):
        module.install(ADDRESS)
    assert host.calls == []
    assert not module.LAUNCHER.exists() or target == "LAUNCHER"


def test_release_resolves_reordered_index(host):
    module.DROPIN.parent.mkdir()
    module.DROPIN.write_text(module.contents(ADDRESS))
    host.output = ("hci0: Primary controller\n addr 00:11:22:33:44:55 version 1\n"
                   "hci7: Primary controller\n addr aa:bb:cc:dd:ee:ff version 1\n")
    module.release()
    assert module.DROPIN.with_suffix(".conf.disabled").read_text() == module.contents(ADDRESS)
    assert [c[0] for c in host.calls] == [
        (module.SYSTEMCTL, "stop", module.SERVICE),
        (module.SYSTEMCTL, "disable", module.SERVICE),
        (module.SYSTEMCTL, "daemon-reload"),
        (module.BTMGMT, "info"),
        (module.BTMGMT, "--index", "7", "power", "on"),
    ]


def test_release_unavailable_never_powers_other_controller(host):
    module.DROPIN.parent.mkdir()
    module.DROPIN.write_text(module.contents(ADDRESS))
    host.output = "hci0: Primary controller\n addr 00:11:22:33:44:55 version 1\n"
    with pytest.raises(RuntimeError, match="exactly one"):
        module.release()
    assert host.calls[-1][0] == (module.BTMGMT, "info")


def test_nonroot_and_invalid_address_do_not_run(host, monkeypatch):
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    monkeypatch.setattr("sys.argv", ["installer", "install", "--controller", ADDRESS])
    with pytest.raises(SystemExit):
        module.main()
    monkeypatch.setattr("sys.argv", ["installer", "install", "--controller", "invalid"])
    with pytest.raises(SystemExit):
        module.main()
    assert host.calls == []


def test_upgrade_old_format_preserves_existing_backup_and_is_idempotent(host):
    module.DROPIN.parent.mkdir()
    old = ("[Unit]\nWants=bluetooth.service\nAfter=bluetooth.service\n"
           "[Service]\nExecStart=\nExecStart=/usr/local/libexec/"
           f"sphere-mesh-controller --controller {ADDRESS}\n")
    module.DROPIN.write_text(old)
    previous = module.DROPIN.with_suffix(".conf.previous")
    previous.write_text("earlier backup\n")
    module.install(ADDRESS)
    assert previous.read_text() == "earlier backup\n"
    backups = list(module.DROPIN.parent.glob("file.conf.previous.*"))
    assert len(backups) == 1 and backups[0].read_text() == old
    result = module.DROPIN.read_text()
    assert "ExecStartPre=/usr/bin/sleep 1\n" in result
    assert "Restart=always\nRestartSec=3\n" in result
    module.install(ADDRESS)
    assert list(module.DROPIN.parent.glob("file.conf.previous.*")) == backups
    assert module.DROPIN.read_text() == result


def test_restart_failure_can_recover(host, monkeypatch):
    import subprocess
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "restart" or (args[1] == "is-active" and len([c for c in calls if c[1] == "is-active"]) == 1):
            raise subprocess.CalledProcessError(1, args, stderr="temporarily busy")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.install(ADDRESS)
    assert len([c for c in calls if c[1] == "is-active"]) == 3
    assert host.now == 2


def test_failed_restart_is_preserved_after_timeout(host, monkeypatch):
    import subprocess
    error = subprocess.CalledProcessError(1, ["restart"], stderr="busy")

    def run(args, **kwargs):
        if args[1] == "restart":
            raise error
        if args[1] == "is-active":
            raise subprocess.CalledProcessError(3, args)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        module.install(ADDRESS)
    assert caught.value is error
    assert host.now == 30
