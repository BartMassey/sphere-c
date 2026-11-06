#!/usr/bin/env python3
from spherosphere.paths import state_path
"""Verify the host Mesh relay and read every current lamp via BlueZ."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from btmesh.access import OP_GENERIC_ONOFF_STATUS, generic_onoff_get
from spherosphere.bluez_mesh import open_bluez_mesh_session
from spherosphere.state import StateStore


async def check(path: Path) -> dict:
    with StateStore(path) as store:
        state = store.load_or_create()
        session = await open_bluez_mesh_session(state, store)
        try:
            result = {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "backend": "BlueZ advertising bearer",
                "host_address": f"0x{session.identity['unicast']:04x}",
                "features": session.features,
                "members": [],
            }
            for key, entry in state["devices"].items():
                if entry.get("superseded_by") or entry.get("enrollment_pending"):
                    continue
                member = {"device": key, "name": entry.get("name")}
                try:
                    reply = await session.node.request(
                        int(key, 16), generic_onoff_get(), OP_GENERIC_ONOFF_STATUS,
                        timeout=5, retries=1,
                    )
                    member.update(reply_source=f"0x{reply.src:04x}", power=bool(reply.params[0]))
                except TimeoutError:
                    member["error"] = "timeout"
                result["members"].append(member)
            await session.refresh_properties()
            result["sequence_number"] = session.properties.get("SequenceNumber")
            result["received_messages"] = len(session.node.received)
            result["seconds_since_last_heard"] = session.properties.get("SecondsSinceLastHeard")
            return result
        finally:
            await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=state_path())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    result = asyncio.run(check(args.state))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
    if any(member.get("error") for member in result["members"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
