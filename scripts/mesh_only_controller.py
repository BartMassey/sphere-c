#!/usr/bin/env python3
"""Temporarily reserve a controller for Mesh.

Only a runtime systemd override is changed. Ordinary bluetoothd and the
USB controller remain running. No Mesh keys or node state are modified.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state/mb-mesh-controller-test.json"
OVERRIDE = Path("/run/systemd/system/bluetooth-mesh.service.d/spherosphere-controller-test.conf")
ADDRESS = "F8:AC:65:82:08:83"
SERVICE = "bluetooth-mesh.service"
PERSISTENT_OVERRIDE = Path("/etc/systemd/system/bluetooth-mesh.service.d/10-spherosphere-controller.conf")


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
    if "SUDO_UID" in os.environ:
        os.chown(STATE, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))


def mac_address(value):
    if not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        raise argparse.ArgumentTypeError("controller must be a Bluetooth MAC address")
    return value.upper()


def controller(address=ADDRESS):
    output = run("btmgmt", "info").stdout
    matches = []
    for section in re.split(r"(?m)^hci", output)[1:]:
        index = re.match(r"(\d+):", section)
        addr = re.search(r"(?im)^\s*addr\s+([0-9a-f:]+)\b", section)
        settings = re.search(r"(?m)^\s*current settings:\s*(.*)$", section)
        if index and addr and addr[1].upper() == address and settings:
            matches.append((int(index[1]), "powered" in settings[1].split()))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one controller with address {address}")
    return matches[0]


def restore(state):
    if PERSISTENT_OVERRIDE.exists():
        raise RuntimeError("Persistent Mesh setup is installed; use spherosphere-bt mesh-controller release")
    address = mac_address(state.get("address", ADDRESS))
    expected = state["override"]
    if OVERRIDE.exists() and OVERRIDE.read_text() != expected:
        raise RuntimeError("Runtime override changed; refusing to overwrite it")
    run("systemctl", "stop", SERVICE)
    OVERRIDE.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    index, _ = controller(address)
    run("btmgmt", "--index", str(index), "power",
        "on" if state["was_powered"] else "off")
    if state["mesh_was_active"]:
        run("systemctl", "start", SERVICE)
    state["active"] = False
    state["restored_index"] = index
    save(state)
    print(f"Restored controller {address} ownership", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "restore"))
    parser.add_argument("--controller", type=mac_address)
    args = parser.parse_args()
    if args.action == "restore" and args.controller is not None:
        parser.error("--controller is only valid with start")
    if args.action == "restore":
        restore(json.loads(STATE.read_text()))
        return
    if OVERRIDE.exists():
        raise RuntimeError("Mesh-only runtime override already exists; inspect or restore it first")
    address = args.controller or ADDRESS
    index, was_powered = controller(address)
    state = {
        "index": index, "address": address, "was_powered": was_powered,
        "mesh_was_active": run("systemctl", "is-active", "--quiet", SERVICE,
                               check=False).returncode == 0,
        "override": "[Service]\nExecStart=\nExecStart=/usr/libexec/bluetooth/bluetooth-meshd --io generic:hci"
                    + str(index) + "\n",
        "active": False,
    }
    save(state)
    try:
        run("systemctl", "stop", SERVICE)
        OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE.write_text(state["override"])
        run("systemctl", "daemon-reload")
        run("btmgmt", "--index", str(index), "power", "off")
        run("systemctl", "start", SERVICE)
        run("systemctl", "is-active", "--quiet", SERVICE)
        state["active"] = True
        save(state)
        print(f"Controller {address} (hci{index}) reserved for Mesh (generic backend)", flush=True)
        print("Ordinary bluetoothd was not stopped", flush=True)
    except BaseException:
        restore(state)
        raise


if __name__ == "__main__":
    main()
