"""Install the per-user daemon without copying active Mesh identities."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

from .state import StateLockedError, StateStore
from .transport import adapter_selector


UNIT = "sphere-mesh.service"


def service_assets() -> Path:
    """Find templates in a checkout or an installed distribution."""
    for directory in (
        Path(__file__).resolve().parents[2] / "packaging/systemd",
        Path(sysconfig.get_path("data")) / "share/sphere-mesh/systemd",
    ):
        if (directory / UNIT).is_file():
            return directory
    raise ValueError("Sphere systemd templates are missing; reinstall the package")


def user_paths() -> tuple[Path, Path, Path]:
    """Resolve standard XDG paths without depending on a login shell."""
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ValueError("XDG_RUNTIME_DIR is missing; run from your user login session")
    return config, state / "sphere-mesh/state.json", Path(runtime) / "sphere-mesh/control.sock"


def absolute_path(value: str | Path) -> Path:
    """Normalize a path and reject characters unsuitable for a unit line."""
    path = Path(value).expanduser().absolute()
    if any(ord(character) < 32 for character in str(path)):
        raise ValueError("service paths must not contain control characters")
    return path


def unit_quote(value: str | Path, *, command=False) -> str:
    """Quote a single systemd argument; no shell is used by the service."""
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def render_override(executable: Path, config: Path, state: Path, socket: Path) -> str:
    """Grant writes only to the selected state and socket directories."""
    arguments = (executable, "--config", config, "--state", state, "--socket", socket)
    writable = dict.fromkeys((state.parent, socket.parent))
    return (
        "[Service]\nExecStart=\nExecStart="
        + " ".join(unit_quote(argument, command=True) for argument in arguments)
        + "\nReadWritePaths=" + " ".join(unit_quote(path) for path in writable) + "\n"
    )


def write_config_file(path: Path, content: str, *, mode=0o600) -> None:
    """Atomically replace a regular configuration, keeping the prior version."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"refusing to replace nonregular file {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        previous = path.with_name(path.name + ".previous")
        if previous.is_symlink() or (previous.exists() and not previous.is_file()):
            raise ValueError(f"refusing to replace nonregular backup {previous}")
        # No state files are passed here: this backs up configuration only.
        fd = os.open(previous, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as destination:
            destination.write(path.read_text())
        previous.chmod(mode)
    temporary = path.with_name(path.name + ".installing")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w") as destination:
            destination.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def systemctl(*arguments: str, check=True):
    return subprocess.run(["systemctl", "--user", *arguments], check=check,
                          capture_output=True, text=True)


def install(args: argparse.Namespace) -> None:
    """Install configuration and optionally start a supervised state owner."""
    if os.geteuid() == 0:
        raise ValueError("install the user unit as your normal user, without sudo")
    config_root, default_state, default_socket = user_paths()
    manifest = config_root / "sphere-mesh/install.json"
    previous = {}
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text())
            if not isinstance(previous, dict) or any(
                key not in previous or not isinstance(previous[key], str)
                for key in ("state", "socket", "executable")
            ):
                raise ValueError("invalid installation paths")
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read installation record {manifest}: {exc}") from exc
    # Changing profile must not silently select a new network state file.
    state = absolute_path(args.state or previous.get("state") or default_state)
    socket = absolute_path(args.socket or previous.get("socket") or default_socket)
    executable = args.executable or previous.get("executable") or shutil.which("sphere-meshd")
    if executable is None:
        candidate = Path(sys.executable).with_name("sphere-meshd")
        executable = candidate if candidate.is_file() else None
    if executable is None:
        raise ValueError("sphere-meshd is not installed; provide --executable PATH")
    executable = absolute_path(executable)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"daemon executable is not executable: {executable}")
    if args.profile == "split" and not args.adapter:
        raise ValueError("the split profile requires --adapter for the ordinary Bluetooth radio")
    if args.profile == "mesh" and args.adapter:
        raise ValueError("use --profile split to select a second ordinary adapter")
    if state == socket:
        raise ValueError("state and socket must have different paths")
    if args.enable_now and systemctl("is-active", "--quiet", UNIT, check=False).returncode != 0:
        try:
            with StateStore(state):
                pass
        except StateLockedError as exc:
            raise ValueError("stop the foreground daemon before enabling the user service") from exc
    config = absolute_path(config_root / "sphere-mesh/daemon.toml")
    unit = config_root / "systemd/user" / UNIT
    assets = service_assets()
    settings = [
        "# Managed by sphere-mesh-service; edit and restart the user unit.",
        "mode = " + json.dumps("proxy" if args.profile == "proxy" else "mesh"),
    ]
    if args.adapter:
        settings.append("adapter = " + json.dumps(args.adapter))
    settings.extend((
        "experimental_enrollment = " + str(args.experimental_enrollment).lower(),
        "verbose = false",
    ))
    write_config_file(config, "\n".join(settings) + "\n")
    write_config_file(unit, (assets / UNIT).read_text(), mode=0o644)
    write_config_file(unit.with_name(UNIT + ".d") / "10-install.conf",
                      render_override(executable, config, state, socket), mode=0o644)
    write_config_file(manifest, json.dumps({
        "state": str(state), "socket": str(socket), "executable": str(executable),
    }, indent=2) + "\n")
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    systemctl("daemon-reload")
    if args.enable_now:
        systemctl("enable", UNIT)
        systemctl("restart", UNIT)
    print(f"Installed {UNIT} with the {args.profile} profile")
    print(f"Configuration: {config}")
    print(f"State (used in place): {state}")
    print(f"Socket: {socket}")
    print(f"Executable: {executable}")
    if not args.enable_now:
        print(f"Start with: systemctl --user enable --now {UNIT}")
    print(f"Logs: journalctl --user -u {UNIT} -f")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="install or update a user service profile")
    setup.add_argument("--profile", choices=("proxy", "mesh", "split"), required=True)
    setup.add_argument("--adapter", type=adapter_selector)
    setup.add_argument("--state", type=Path, help="use this existing state in place; never copy it")
    setup.add_argument("--socket", type=Path)
    setup.add_argument("--executable", type=Path)
    setup.add_argument("--experimental-enrollment", action="store_true")
    setup.add_argument("--enable-now", action="store_true")
    for action in ("status", "restart", "stop"):
        commands.add_parser(action)
    commands.add_parser("logs")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install(args)
        elif args.command == "logs":
            return subprocess.call(["journalctl", "--user", "-u", UNIT, "-f"])
        else:
            result = systemctl(args.command, UNIT, check=False)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        message = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"sphere-mesh-service: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
