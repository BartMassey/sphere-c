"""Check user-service installation without touching systemd or real state."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sphere_mesh import user_service as module
from sphere_mesh.service_config import load_service_config
from sphere_mesh.state import StateStore, new_state


@pytest.fixture
def installation(tmp_path, monkeypatch):
    config = tmp_path / "config"
    state = tmp_path / "state/state.json"
    socket = tmp_path / "runtime/control.sock"
    executable = tmp_path / 'program with $dollar and %percent'
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setattr(module, "user_paths", lambda: (config, state, socket))
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    systemctl = Mock(return_value=SimpleNamespace(returncode=3, stdout="", stderr=""))
    monkeypatch.setattr(module, "systemctl", systemctl)
    return SimpleNamespace(config=config, state=state, socket=socket,
                           executable=executable, systemctl=systemctl)


@pytest.mark.parametrize("profile,adapter,mode", [
    ("proxy", None, "proxy"),
    ("mesh", None, "mesh"),
    ("split", "AA:BB:CC:DD:EE:FF", "mesh"),
])
def test_installs_each_profile_and_keeps_existing_state_in_place(installation, profile, adapter, mode):
    with StateStore(installation.state) as store:
        store.save(new_state())
    original = installation.state.read_bytes()
    args = ["install", "--profile", profile, "--executable", str(installation.executable),
            "--enable-now"]
    if adapter:
        args += ["--adapter", adapter]
    assert module.main(args) == 0
    assert installation.state.read_bytes() == original
    config = load_service_config(installation.config / "sphere-mesh/daemon.toml")
    assert config["mode"] == mode
    assert config.get("adapter") == adapter
    assert not config["experimental_enrollment"]
    assert (installation.config / "sphere-mesh/daemon.toml").stat().st_mode & 0o777 == 0o600
    override = installation.config / "systemd/user/sphere-mesh.service.d/10-install.conf"
    text = override.read_text()
    assert "$$dollar" in text and "%%percent" in text
    assert str(installation.state) in text
    assert str(installation.socket) in text
    assert str(installation.state.parent) in text.split("ReadWritePaths=", 1)[1]
    assert installation.systemctl.call_args_list[-3:] == [
        (("daemon-reload",),), (("enable", module.UNIT),), (("restart", module.UNIT),),
    ]


def test_live_foreground_owner_must_be_stopped_before_activation(installation):
    with StateStore(installation.state):
        assert module.main(["install", "--profile", "proxy", "--enable-now",
                            "--executable", str(installation.executable)]) == 1
    assert not (installation.config / "sphere-mesh/daemon.toml").exists()
    assert installation.systemctl.call_count == 1


@pytest.mark.parametrize("arguments", [
    ["--profile", "split"],
    ["--profile", "mesh", "--adapter", "hci1"],
])
def test_invalid_hardware_arrangements_do_not_write_configuration(installation, arguments):
    assert module.main(["install", *arguments, "--executable", str(installation.executable)]) == 1
    assert not installation.config.exists()


def test_config_replacement_preserves_previous_settings_and_never_follows_symlink(tmp_path):
    path = tmp_path / "settings.toml"
    module.write_config_file(path, "mode='proxy'\n")
    module.write_config_file(path, "mode='mesh'\n")
    assert path.with_name("settings.toml.previous").read_text() == "mode='proxy'\n"
    victim = tmp_path / "state.json"
    victim.write_text("must not change")
    path.unlink()
    path.symlink_to(victim)
    with pytest.raises(ValueError, match="nonregular"):
        module.write_config_file(path, "overwrite")
    assert victim.read_text() == "must not change"


def test_installed_assets_are_packaged():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    assert metadata["project"]["scripts"]["sphere-mesh-service"] == "sphere_mesh.user_service:main"
    assets = metadata["tool"]["setuptools"]["data-files"]["share/sphere-mesh/systemd"]
    assert {"packaging/systemd/sphere-mesh.service", "packaging/systemd/proxy.toml",
            "packaging/systemd/mesh.toml", "packaging/systemd/split.toml"} <= set(assets)


def test_profile_change_reuses_installed_state_socket_and_executable(installation, tmp_path):
    state = tmp_path / 'existing-network/state.json'
    socket = tmp_path / 'existing-runtime/sphere.sock'
    with StateStore(state) as store:
        store.save(new_state())
    original = state.read_bytes()
    assert module.main(['install', '--profile', 'split', '--adapter', 'hci1',
                        '--state', str(state), '--socket', str(socket),
                        '--executable', str(installation.executable)]) == 0
    assert module.main(['install', '--profile', 'proxy']) == 0
    assert state.read_bytes() == original
    override = installation.config / 'systemd/user/sphere-mesh.service.d/10-install.conf'
    assert str(state) in override.read_text() and str(socket) in override.read_text()
    assert not installation.state.exists()
