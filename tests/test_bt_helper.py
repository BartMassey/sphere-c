"""The Bluetooth helper exposes fixed tools without shell execution."""

import os
from pathlib import Path
import re
import runpy
import signal
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def helper(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/sphere-bt"))
    main = namespace["main"]
    process = Mock(return_value=SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setitem(main.__globals__, "ROOT", tmp_path)
    monkeypatch.setitem(main.__globals__, "subprocess", SimpleNamespace(run=process))

    def invoke(*arguments):
        monkeypatch.setattr("sys.argv", ["sphere-bt", *arguments])
        return main()

    return SimpleNamespace(invoke=invoke, process=process, root=tmp_path,
                           globals=main.__globals__)


def test_capture_creates_private_files_and_runs_bounded_fixed_tool(helper):
    previous = os.umask(0)
    try:
        helper.invoke("capture", "relay-trial", "--seconds", "17")
    finally:
        os.umask(previous)

    path = helper.root / "state/captures/relay-trial.btsnoop"
    log = helper.root / "state/captures/relay-trial.btmon.txt"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    helper.process.assert_called_once()
    call = helper.process.call_args
    assert call.args == ([
        "sudo", "-n", "timeout", "--signal=INT", "17",
        "btmon", "--write", str(path),
    ],)
    assert call.kwargs["stdout"] is call.kwargs["stderr"]
    assert call.kwargs["stdout"].name == str(log)
    assert call.kwargs["stdout"].closed
    assert call.kwargs["check"] is False
    assert not call.kwargs.get("shell", False)


def test_client_options_are_forwarded_in_order(helper):
    helper.invoke("ctl", "--device", "ARI", "control", "read")
    assert helper.process.call_args.args[0] == [
        str(helper.root / "bin/sphere-meshctl"), "--device", "ARI", "control", "read",
    ]


def test_cancel_only_targets_the_fixed_bluetooth_diagnostic(helper):
    helper.invoke("cancel-hci-test")
    assert helper.process.call_args.args[0] == [
        "sudo", "-n", "pkill", "-TERM", "-f", "^btmgmt --index 0 hci-cmd( |$)",
    ]
    assert not helper.process.call_args.kwargs.get("shell", False)


def test_archive_uses_only_the_fixed_evidence_script(helper):
    helper.invoke("archive-evidence")
    assert helper.process.call_args.args[0] == [
        "sudo", "-n", "python3", str(helper.root / "scripts/archive_bluez_issue.py"),
    ]


def test_mesh_only_forwards_selected_controller(helper):
    helper.invoke("mesh-only", "start", "--controller", "aa:bb:cc:dd:ee:ff")
    assert helper.process.call_args.args[0] == [
        "sudo", "-n", "python3", str(helper.root / "scripts/mesh_only_controller.py"),
        "start", "--controller", "AA:BB:CC:DD:EE:FF",
    ]


@pytest.mark.parametrize("args", [
    ("mesh-only", "restore", "--controller", "AA:BB:CC:DD:EE:FF"),
    ("mesh-only", "start", "--controller", "bad"),
    ("daemon", "--mode", "mesh", "--backend", "bluez"),
    ("daemon", "--adapter", "hci;touch"),
    ("daemon", "--unknown"),
])
def test_invalid_new_options_are_rejected_before_execution(helper, args):
    with pytest.raises(SystemExit):
        helper.invoke(*args)
    helper.process.assert_not_called()


@pytest.mark.parametrize("args, expected", [
    ((), ["--mode", "proxy"]),
    (("--backend", "bluez"), ["--backend", "bluez"]),
    (("--mode", "mesh", "--adapter", "hci2", "--experimental-enrollment",
      "--enroll-seconds", "12", "--state", "/tmp/state", "--socket", "/tmp/socket",
      "--verbose"),
     ["--mode", "mesh", "--adapter", "hci2", "--enroll-seconds", "12",
      "--state", "/tmp/state", "--socket", "/tmp/socket",
      "--experimental-enrollment", "--verbose"]),
])
def test_daemon_forwards_only_supported_flags(helper, args, expected):
    helper.invoke("daemon", *args)
    assert helper.process.call_args.args[0] == [str(helper.root / "bin/sphere-meshd"), *expected]
    assert not helper.process.call_args.kwargs.get("shell", False)


def test_journal_filters_are_arguments_to_the_mesh_unit(helper):
    helper.invoke("logs", "--mesh-only", "--since", "2026-11-01",
                  "--until", "2026-11-03", "--lines", "500")
    assert helper.process.call_args.args[0] == [
        "sudo", "-n", "journalctl", "-u", "bluetooth-mesh.service",
        "--since", "2026-11-01", "--until", "2026-11-03",
        "-n", "500", "--no-pager",
    ]
    assert not helper.process.call_args.kwargs.get("shell", False)


@pytest.mark.parametrize("name", ["../escape", "nested/capture", ".hidden", "", "/tmp/escape"])
@pytest.mark.parametrize("command", ["capture", "probe"])
def test_output_names_reject_traversal_before_execution(helper, name, command):
    args = [command, name] if command == "capture" else [command, "proxy", name]
    with pytest.raises(SystemExit) as failure:
        helper.invoke(*args)
    assert failure.value.code == 2
    helper.process.assert_not_called()
    assert not (helper.root / "state").exists()


@pytest.mark.parametrize("seconds", ["0", "301", "-1"])
def test_capture_rejects_unbounded_or_invalid_duration(helper, seconds):
    with pytest.raises(SystemExit) as failure:
        helper.invoke("capture", "trial", "--seconds", seconds)
    assert failure.value.code == 2
    helper.process.assert_not_called()


def test_capture_never_overwrites_existing_recording(helper):
    path = helper.root / "state/captures/trial.btsnoop"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"retain capture")
    with pytest.raises(FileExistsError):
        helper.invoke("capture", "trial")
    assert path.read_bytes() == b"retain capture"
    helper.process.assert_not_called()


def test_proxy_probe_uses_fixed_project_script_and_keeps_flags_as_arguments(helper):
    helper.invoke("probe", "proxy", "gatt-check", "--pause-advertising", "--resume-before-read")
    assert helper.process.call_args.args == ([
        str(helper.root / ".venv/bin/python"),
        str(helper.root / "scripts/check_mesh_members.py"),
        "--output", str(helper.root / "state/captures/gatt-check.json"),
        "--pause-advertising", "--resume-before-read",
    ],)
    assert helper.process.call_args.kwargs == {"cwd": helper.root, "check": True}


def test_management_runs_only_btmgmt_without_interpreting_shell_text(helper):
    helper.invoke("mgmt", "info", ";", "touch", "unexpected-file")
    assert helper.process.call_args.args == ([
        "sudo", "-n", "btmgmt", "info", ";", "touch", "unexpected-file",
    ],)
    assert helper.process.call_args.kwargs == {"cwd": helper.root, "check": True}


def test_control_passes_arguments_to_fixed_unprivileged_project_client(helper):
    helper.invoke("ctl", "control", "read", ";", "arbitrary-command")
    assert helper.process.call_args.args == ([
        str(helper.root / "bin/sphere-meshctl"), "control", "read", ";", "arbitrary-command",
    ],)
    assert not helper.process.call_args.kwargs.get("shell", False)


def test_service_selection_rejects_unrelated_system_units(helper):
    with pytest.raises(SystemExit) as failure:
        helper.invoke("service", "stop", "ssh.service")
    assert failure.value.code == 2
    helper.process.assert_not_called()


def test_daemon_stop_uses_exact_project_module_pattern_and_sigterm_only(helper, monkeypatch):
    helper.process.return_value = SimpleNamespace(returncode=0, stdout="123\n456\n")
    kill = Mock()
    monkeypatch.setattr(helper.globals["os"], "kill", kill)
    helper.invoke("daemon-stop")
    call = helper.process.call_args
    assert call.args[0][:2] == ["pgrep", "-f"]
    assert call.kwargs == {"capture_output": True, "text": True}
    pattern = call.args[0][2]
    # pgrep uses POSIX ERE; Python's noncapturing-group extension is invalid.
    assert "(?" not in pattern
    executable = str(helper.root / ".venv/bin/python")
    assert re.search(pattern, executable + " -m sphere_mesh.daemon")
    assert re.search(pattern, executable + " -m sphere_mesh.daemon --verbose")
    for other in (
        "python -m sphere_mesh.daemon",
        str(helper.root / "other/.venv/bin/python") + " -m sphere_mesh.daemon",
        executable + " -m sphere_mesh.daemon_other",
        executable + " -m sphere_meshXdaemon",
        executable + " unrelated.py",
        "prefix " + executable + " -m sphere_mesh.daemon",
    ):
        assert not re.search(pattern, other), other
    assert kill.call_args_list == [
        ((123, signal.SIGTERM),), ((456, signal.SIGTERM),),
    ]


@pytest.mark.parametrize("returncode", [1, 2])
def test_daemon_stop_never_kills_after_failed_process_query(helper, monkeypatch, returncode):
    helper.process.return_value = SimpleNamespace(returncode=returncode, stdout="")
    kill = Mock()
    monkeypatch.setattr(helper.globals["os"], "kill", kill)
    if returncode == 2:
        with pytest.raises(SystemExit, match="Could not identify"):
            helper.invoke("daemon-stop")
    else:
        helper.invoke("daemon-stop")
    kill.assert_not_called()
