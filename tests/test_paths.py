"""Verify that all entry points share paths without depending on a checkout."""

from pathlib import Path

import pytest

from spherosphere import client, daemon, paths
from spherosphere.gui import app as gui_app


def test_all_clients_and_daemon_agree_after_changing_directory(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state/sphere_state.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    expected = tmp_path / "runtime/spherosphere/control.sock"
    assert paths.socket_path() == expected
    assert client.parse_args(["health"]).socket == expected
    assert gui_app.parse_args([])[0].socket == expected
    assert daemon.parse_args([]).socket == expected
    assert daemon.parse_args([]).state == tmp_path / "state/spherosphere/state.json"


def test_xdg_fallbacks_ignore_relative_and_empty_values(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert paths.config_path() == tmp_path / ".config/spherosphere/daemon.toml"
    assert paths.state_path() == tmp_path / ".local/state/spherosphere/state.json"
    assert paths.socket_path() == tmp_path / ".local/state/spherosphere/runtime/control.sock"
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative")
    assert paths.socket_path() == tmp_path / ".local/state/spherosphere/runtime/control.sock"


def test_config_then_environment_then_cli_precedence(tmp_path, monkeypatch):
    config = paths.config_path()
    config.parent.mkdir(parents=True)
    state, socket = tmp_path / "network.json", tmp_path / "control.sock"
    config.write_text(f'mode="mesh"\nstate="{state}"\nsocket="{socket}"\n')
    assert daemon.parse_args([]).mode == "mesh"
    assert daemon.parse_args([]).state == state
    assert daemon.parse_args([]).socket == socket
    assert client.parse_args(["health"]).socket == socket
    assert gui_app.parse_args([])[0].socket == socket
    overridden = tmp_path / "override.sock"
    monkeypatch.setenv("SPHEROSPHERE_SOCKET", str(overridden))
    assert daemon.parse_args([]).socket == overridden
    assert gui_app.parse_args([])[0].socket == overridden
    assert client.parse_args(["health"]).socket == overridden
    assert daemon.parse_args(["--socket", str(socket)]).socket == socket
    assert client.parse_args(["--socket", str(socket), "health"]).socket == socket


def test_explicit_config_is_shared_without_touching_mesh_state(tmp_path, monkeypatch):
    config = tmp_path / "custom.toml"
    endpoint = tmp_path / "custom.sock"
    config.write_text(f'socket="{endpoint}"\n')
    monkeypatch.setenv("SPHEROSPHERE_CONFIG", str(config))
    assert paths.socket_path() == endpoint
    assert daemon.parse_args([]).socket == endpoint
    assert not paths.state_path().exists()


@pytest.mark.parametrize("value", ['socket="relative"', 'socket=3', 'broken = '])
def test_invalid_connection_settings_are_not_silently_ignored(tmp_path, value):
    config = paths.config_path()
    config.parent.mkdir(parents=True)
    config.write_text(value)
    with pytest.raises(ValueError):
        paths.socket_path()
