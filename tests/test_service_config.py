from pathlib import Path

import pytest

from sphere_mesh import daemon


def config_file(tmp_path, text):
    path = tmp_path / "daemon.toml"
    path.write_text(text)
    return path


def test_config_and_cli_overrides(tmp_path):
    path = config_file(tmp_path, '''
mode = "mesh"
adapter = "aa:bb:cc:dd:ee:ff"
verbose = true
experimental_enrollment = true
enroll_seconds = 30
''')
    args = daemon.parse_args(["--config", str(path)])
    assert (args.mode, args.backend, args.adapter) == ("mesh", "bluez", "AA:BB:CC:DD:EE:FF")
    assert args.verbose and args.experimental_enrollment and args.enroll_seconds == 30
    args = daemon.parse_args([
        "--config", str(path), "--backend", "proxy", "--adapter", "hci1",
        "--state", "relative.json", "--no-verbose", "--no-experimental-enrollment",
        "--enroll-seconds", "0",
    ])
    assert (args.mode, args.backend, args.adapter) == ("proxy", "proxy", "hci1")
    assert args.state == Path("relative.json")
    assert not args.verbose and not args.experimental_enrollment and args.enroll_seconds == 0


@pytest.mark.parametrize("contents", [
    'backend = "bluez"', 'mode = "bluez"', 'mode = 1',
    'adapter = "bad"', 'adapter = 1', 'state = "relative"',
    'socket = "/tmp/socket"', 'verbose = 1', 'experimental_enrollment = "true"',
    'enroll_seconds = true', 'enroll_seconds = 3601', '[mode]\nx = 1',
    'mode = ',
])
def test_invalid_config_fails_before_startup(tmp_path, contents):
    path = config_file(tmp_path, contents)
    with pytest.raises(SystemExit) as exc:
        daemon.parse_args(["--config", str(path)])
    assert exc.value.code == 2


def test_missing_explicit_config_fails(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        daemon.parse_args(["--config", str(tmp_path / "missing.toml")])
    assert exc.value.code == 2
    assert "cannot read config" in capsys.readouterr().err


def test_mesh_config_enrollment_requires_explicit_enable(tmp_path):
    path = config_file(tmp_path, 'mode = "mesh"\nenroll_seconds = 1\n')
    with pytest.raises(SystemExit) as exc:
        daemon.parse_args(["--config", str(path)])
    assert exc.value.code == 2
