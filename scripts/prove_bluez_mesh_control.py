#!/usr/bin/env python3
from spherosphere.paths import state_path
"""Exercise both configured lamps through the BlueZ Mesh advertising bearer."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from spherosphere.bluez_mesh import open_bluez_mesh_session
from spherosphere.control import BasicController
from spherosphere.state import StateStore


class VerificationError(Exception):
    """A lamp's independent status did not match the requested state."""


def progress(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


async def prove(state_path, seconds=60, hold_seconds=3, record=None):
    """Run the proof, recording only public status and sanitized failures."""
    result = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "backend": "BlueZ Mesh advertising bearer",
        "controller": "external BlueZ Mesh node",
        "host_address": None, "features": {}, "sequence_start": None,
        "sequence_end": None, "members": {}, "baseline": {}, "stages": [],
        "stability": [], "failures": [], "restore": {"status": "not_needed"},
        "success": False,
    }
    phase, dst = "state", None
    session = None
    baseline = {}
    controllers = {}
    mutated = False

    def save():
        if record:
            record(result)

    def failure(exc):
        result["failures"].append({"phase": phase, "dst": dst,
                                   "exception_type": type(exc).__name__})
        progress("failure", phase=phase, dst=dst, exception_type=type(exc).__name__)
        save()

    def check(status, expected):
        if status.present != expected or (status.target is not None and status.target != expected):
            raise VerificationError()

    async def restore():
        nonlocal phase, dst
        result["restore"] = {"status": "in_progress", "members": {}}
        for name in ("ARI", "BEN"):
            dst = result["members"][name]
            item = result["restore"]["members"].setdefault(name, {})
            try:
                phase = "restore_set"
                ack = await controllers[name].set(baseline[name])
                item["set_ack"] = ack.present
                check(ack, baseline[name])
            except Exception as exc:
                failure(exc)
                item["set_error"] = type(exc).__name__
            try:
                phase = "restore_readback"
                value = await controllers[name].read()
                item["readback"] = value.present
                check(value, baseline[name])
                item["verified"] = True
            except Exception as exc:
                failure(exc)
                item["verified"] = False
                item["read_error"] = type(exc).__name__
        ok = all(item.get("verified") and "set_error" not in item
                 for item in result["restore"]["members"].values())
        result["restore"]["status"] = "verified" if ok else "failed"
        progress("restore", status=result["restore"]["status"])
        save()

    try:
        with StateStore(Path(state_path)) as store:
            try:
                state = store.load_or_create()
                for name in ("ARI", "BEN"):
                    matches = [(key, entry) for key, entry in state["devices"].items()
                               if entry.get("name") == name and not entry.get("superseded_by")
                               and not entry.get("enrollment_pending")]
                    if len(matches) != 1 or not matches[0][1].get("official_config_complete"):
                        raise VerificationError()
                    result["members"][name] = matches[0][0]
                phase = "open_session"
                session = await open_bluez_mesh_session(state, store)
                result["host_address"] = f"0x{session.identity['unicast']:04x}"
                result["features"] = session.features
                await session.refresh_properties()
                result["sequence_start"] = session.properties.get("SequenceNumber")
                controllers = {name: BasicController(session.node, int(address, 16),
                                lambda: store.allocate_tid(state))
                               for name, address in result["members"].items()}
                for name in ("ARI", "BEN"):
                    phase, dst = "baseline", result["members"][name]
                    status = await controllers[name].read()
                    if status.target is not None and status.target != status.present:
                        raise VerificationError()
                    baseline[name] = status.present
                    result["baseline"][name] = status.present
                progress("baseline", **baseline)
                save()
                for label, desired in (("ari_on_ben_off", {"ARI": True, "BEN": False}),
                                       ("ari_off_ben_on", {"ARI": False, "BEN": True}),
                                       ("both_on", {"ARI": True, "BEN": True})):
                    stage = {"name": label, "desired": desired, "set_ack": {}, "readback": {}}
                    result["stages"].append(stage)
                    for name in ("ARI", "BEN"):
                        phase, dst = "stage_set", result["members"][name]
                        mutated = True
                        ack = await controllers[name].set(desired[name])
                        stage["set_ack"][name] = ack.present
                        check(ack, desired[name])
                    for name in ("ARI", "BEN"):
                        phase, dst = "stage_readback", result["members"][name]
                        value = await controllers[name].read()
                        stage["readback"][name] = value.present
                        check(value, desired[name])
                    stage["verified"] = True
                    progress("stage", name=label, verified=True)
                    save()
                    await asyncio.sleep(hold_seconds)
                await restore()
                if result["restore"]["status"] != "verified":
                    raise VerificationError()
                phase, dst = "stability", None
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))
                    sample = {}
                    result["stability"].append(sample)
                    for name in ("ARI", "BEN"):
                        phase, dst = "stability_readback", result["members"][name]
                        value = await controllers[name].read()
                        sample[name] = value.present
                        check(value, baseline[name])
                    progress("stability", sample=len(result["stability"]), verified=True)
                    save()
                result["success"] = not result["failures"]
            except Exception as exc:
                failure(exc)
            finally:
                if session is not None:
                    if mutated and (not result["success"] or result["restore"]["status"] != "verified"):
                        await restore()
                    try:
                        phase, dst = "sequence_end", None
                        await session.refresh_properties()
                        result["sequence_end"] = session.properties.get("SequenceNumber")
                    except Exception as exc:
                        failure(exc)
                    try:
                        phase = "close_session"
                        await session.close()
                    except Exception as exc:
                        failure(exc)
    except Exception as exc:
        failure(exc)
    result["success"] = result["success"] and not result["failures"]
    save()
    progress("result", success=result["success"], restore=result["restore"]["status"])
    return result


def bounded(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=bounded(1, 180), default=60)
    parser.add_argument("--hold-seconds", type=bounded(1, 10), default=3)
    args = parser.parse_args()
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        def record(result):
            stream.seek(0)
            json.dump(result, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        record({"success": False, "status": "starting"})
        result = asyncio.run(prove(state_path(), args.seconds,
                                   args.hold_seconds, record))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
