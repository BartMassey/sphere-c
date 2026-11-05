#!/usr/bin/env python3
"""Read ARI and BEN through one shared Mesh Proxy connection.

Uses the existing locked controller state and advances its sequence cursor.
No provisioning or lighting Set operations are performed.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

from btmesh.access import (
    OP_GENERIC_ONOFF_STATUS, encode_opcode, generic_onoff_get,
    parse_generic_onoff_status,
)
from btmesh.crypto import k3
from sphere_mesh.state import StateStore
from sphere_mesh.bluez_mesh import open_bluez_mesh_session
from sphere_mesh.transport import (
    LocalTransport, find_proxy_node, open_mesh_proxy_session,
)


async def check(state_path: Path, *, pause_advertising=False, resume_before_read=False) -> dict:
    store = StateStore(state_path)
    store.acquire()
    transport = LocalTransport()
    stack = AsyncExitStack()
    scanner = None
    session = None
    try:
        state = store.load_or_create()
        restore_radio = None
        if pause_advertising:
            host = await open_bluez_mesh_session(state, store)
            stack.push_async_callback(host.close)
            radio = host.pause_advertising()
            await radio.__aenter__()
            paused = True

            async def restore_radio():
                nonlocal paused
                if paused:
                    await radio.__aexit__(None, None, None)
                    paused = False

            stack.push_async_callback(restore_radio)
        members = []
        for name in ("ARI", "BEN"):
            matches = [(int(key, 16), item) for key, item in state["devices"].items()
                       if item.get("name") == name and not item.get("superseded_by")]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one current device named {name}")
            members.append((name, *matches[0]))
        await transport.start()
        netkey = bytes.fromhex(state["netkey"])
        scanner = transport.scanner()
        proxy, _ = await find_proxy_node(
            scanner, k3(netkey), timeout=30, stop_after=False,
            identity_netkey=netkey,
            node_addresses=tuple(address for _, address, _ in members),
        )
        if proxy is None:
            raise RuntimeError("No matching local Mesh proxy found")

        def persist_seq(next_seq: int) -> None:
            store.advance_sequence(state, next_seq)

        _, address, entry = members[0]
        session = await open_mesh_proxy_session(
            transport, proxy.device, netkey=netkey,
            appkey=bytes.fromhex(state["appkey"]),
            device_key=bytes.fromhex(entry["device_key"]),
            iv_index=state["iv_index"], provisioner_addr=state["provisioner_addr"],
            unicast=address, seq=state["seq"], persist_seq=persist_seq,
        )
        for _, address, entry in members[1:]:
            session.node.add_device(address, bytes.fromhex(entry["device_key"]))
        if resume_before_read and restore_radio is not None:
            await restore_radio()
            if not host.features.get("Relay"):
                raise RuntimeError("Mesh relay did not resume")
        result = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "operation": "two AppKey OnOff Gets over one GATT session",
            "proxy_address": proxy.device.address,
            "relay_resumed_before_reads": resume_before_read,
            "members": [],
        }
        for name, address, _ in members:
            reply = await session.node.request(
                address, generic_onoff_get(), OP_GENERIC_ONOFF_STATUS,
                timeout=8, retries=0,
            )
            status = parse_generic_onoff_status(encode_opcode(reply.opcode) + reply.params)
            result["members"].append({"name": name, "primary": f"0x{address:04x}",
                                      "reply_source": f"0x{reply.src:04x}",
                                      "power": status.present_onoff})
        return result
    finally:
        if session is not None:
            await session.close()
        if scanner is not None:
            try:
                await scanner.stop()
            except Exception:
                pass
        await transport.stop()
        await stack.aclose()
        store.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("state/sphere_state.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause-advertising", action="store_true")
    parser.add_argument("--resume-before-read", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    if args.resume_before_read and not args.pause_advertising:
        parser.error("--resume-before-read requires --pause-advertising")
    result = asyncio.run(check(
        args.state, pause_advertising=args.pause_advertising,
        resume_before_read=args.resume_before_read,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
