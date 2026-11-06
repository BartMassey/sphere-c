#!/usr/bin/env python3
from spherosphere.paths import state_path
"""Record read-only vendor response bursts using the existing Mesh node.

Stop spherosphere.service first. This takes the normal state lock and
attaches the existing BlueZ identity; it never copies keys or state.
No lighting, provisioning, or configuration Set is sent.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from spherosphere.bluez_mesh import open_bluez_mesh_session
from spherosphere.state import StateStore
from spherosphere.vendor import tuya_dp_read, vendor_model_element


async def probe(output: Path) -> None:
    result = {"utc": datetime.now(timezone.utc).isoformat(), "trials": []}
    with StateStore(state_path()) as store:
        state = store.load_or_create()
        session = await open_bluez_mesh_session(state, store)
        try:
            loop = asyncio.get_running_loop()
            for key, entry in state["devices"].items():
                if entry.get("superseded_by") or entry.get("enrollment_pending"):
                    continue
                address = vendor_model_element(entry["composition"])
                for ids, command_type in (([102], 1), ([2, 102], 1),
                                           ([7, 101, 102, 103, 104], 1), ([102], 5)):
                    payload = tuya_dp_read(ids, command_type)
                    trial = {"name": entry.get("name"), "destination": address,
                             "request": payload.hex(), "window_seconds": 5,
                             "messages": []}
                    result["trials"].append(trial)
                    started = loop.time()

                    def received(message):
                        trial["messages"].append({
                            "seconds": round(loop.time() - started, 4),
                            "source": message.src, "opcode": message.opcode,
                            "params": message.params.hex(),
                        })

                    session.node.on_message = received
                    await session.send(address, payload, dev_key=False, ttl=7)
                    await asyncio.sleep(5)
                    print(json.dumps(trial), flush=True)
        finally:
            session.node.on_message = None
            await session.close()
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    asyncio.run(probe(args.output))
