#!/usr/bin/env python3
"""Install or release the dedicated Bluetooth Mesh controller service."""

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE = (SCRIPT_DIR / "sphere-mesh-controller" if
          SCRIPT_DIR.name == "systemd" else
          SCRIPT_DIR.parent / "packaging/systemd/sphere-mesh-controller")
LAUNCHER = Path("/usr/local/libexec/sphere-mesh-controller")
DROPIN = Path("/etc/systemd/system/bluetooth-mesh.service.d/10-sphere-controller.conf")
LEGACY = Path("/run/systemd/system/bluetooth-mesh.service.d/sphere-controller-test.conf")
SYSTEMCTL = "/usr/bin/systemctl"
BTMGMT = "/usr/bin/btmgmt"
SERVICE = "bluetooth-mesh.service"
MAC = r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}"
MANAGED = re.compile(
    r"\[Unit\]\nWants=bluetooth\.service\nAfter=bluetooth\.service\n"
    r"\[Service\]\nExecStart=\nExecStart=/usr/local/libexec/"
    r"sphere-mesh-controller --controller (" + MAC + r")\n"
    r"(?:ExecStartPre=/usr/bin/sleep 1\nRestart=always\nRestartSec=3\n)?\Z")
LEGACY_FORMAT = re.compile(
    r"\[Service\]\nExecStart=\nExecStart=/usr/libexec/bluetooth/"
    r"bluetooth-meshd --io generic:hci[0-9]+\n\Z")


def mac_address(value):
    value = value.upper()
    if not re.fullmatch(MAC, value):
        raise argparse.ArgumentTypeError("controller must be a Bluetooth MAC address")
    return value


def regular_or_absent(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise RuntimeError(f"refusing non-regular file: {path}")
    return True


def absent(path):
    if path.is_symlink() or path.exists():
        raise RuntimeError(f"refusing to overwrite: {path}")


def write_file(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sphere-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def wait_for_service(restart_error):
    """Allow systemd restart recovery and require two active checks."""
    deadline = time.monotonic() + 30
    consecutive = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            subprocess.run((SYSTEMCTL, "is-active", "--quiet", SERVICE),
                           check=True, capture_output=True, text=True,
                           timeout=min(remaining, 5))
            consecutive += 1
            if consecutive == 2:
                return
        except (OSError, subprocess.SubprocessError):
            consecutive = 0
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    if restart_error is not None:
        raise restart_error
    raise RuntimeError("bluetooth-mesh.service did not remain active within 30 seconds")


def contents(address):
    return ("[Unit]\nWants=bluetooth.service\nAfter=bluetooth.service\n"
            "[Service]\nExecStart=\nExecStart=/usr/local/libexec/"
            f"sphere-mesh-controller --controller {address}\n"
            "ExecStartPre=/usr/bin/sleep 1\nRestart=always\nRestartSec=3\n")


def install(address):
    regular_or_absent(LAUNCHER)
    existing = regular_or_absent(DROPIN)
    if existing:
        if not MANAGED.fullmatch(DROPIN.read_text()):
            raise RuntimeError("existing drop-in is not managed by this installer")

    legacy = regular_or_absent(LEGACY)
    if legacy:
        if not LEGACY_FORMAT.fullmatch(LEGACY.read_text()):
            raise RuntimeError("legacy runtime drop-in has unexpected contents")
        absent(LEGACY.with_suffix(LEGACY.suffix + ".previous"))
    if not regular_or_absent(SOURCE):
        raise RuntimeError("launcher source missing")
    source = SOURCE.read_bytes()
    write_file(LAUNCHER, source, 0o755)
    new_content = contents(address)
    if existing and DROPIN.read_text() != new_content:
        backup = DROPIN.with_suffix(DROPIN.suffix + ".previous")
        if backup.is_symlink() or backup.exists():
            fd, name = tempfile.mkstemp(prefix=backup.name + ".", dir=backup.parent)
            os.close(fd)
            backup = Path(name)
        DROPIN.rename(backup)
    write_file(DROPIN, new_content.encode(), 0o644)
    if legacy:
        LEGACY.rename(LEGACY.with_suffix(LEGACY.suffix + ".previous"))
    run(SYSTEMCTL, "daemon-reload")
    run(SYSTEMCTL, "enable", SERVICE)
    restart_error = None
    try:
        run(SYSTEMCTL, "restart", SERVICE)
    except (OSError, subprocess.SubprocessError) as exc:
        restart_error = exc
    wait_for_service(restart_error)


def controller_index(output, address):
    headers = list(re.finditer(r"(?m)^hci([0-9]+):", output))
    matches = []
    for pos, header in enumerate(headers):
        end = headers[pos + 1].start() if pos + 1 < len(headers) else len(output)
        section = output[header.end():end]
        addresses = re.findall(r"(?im)^\s*addr\s+([0-9a-f:]+)\b", section)
        if address in [item.upper() for item in addresses]:
            matches.append(int(header.group(1)))
    if len(matches) != 1:
        raise RuntimeError("expected exactly one matching controller")
    return matches[0]


def release():
    if not regular_or_absent(DROPIN):
        raise RuntimeError("managed drop-in is missing")
    match = MANAGED.fullmatch(DROPIN.read_text())
    if not match:
        raise RuntimeError("existing drop-in is not managed by this installer")
    disabled = DROPIN.with_suffix(DROPIN.suffix + ".disabled")
    absent(disabled)
    run(SYSTEMCTL, "stop", SERVICE)
    run(SYSTEMCTL, "disable", SERVICE)
    DROPIN.rename(disabled)
    run(SYSTEMCTL, "daemon-reload")
    output = run(BTMGMT, "info").stdout
    index = controller_index(output, match.group(1))
    run(BTMGMT, "--index", str(index), "power", "on")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "release"))
    parser.add_argument("--controller", type=mac_address)
    args = parser.parse_args()
    if (args.action == "install") != (args.controller is not None):
        parser.error("--controller is required for install and forbidden for release")
    if os.geteuid() != 0:
        parser.error("must run as root")
    try:
        if args.action == "install":
            install(args.controller)
        else:
            release()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None)
        suffix = f": {detail.strip()}" if detail else ""
        parser.exit(1, f"{parser.prog}: {exc}{suffix}\n")


if __name__ == "__main__":
    main()
