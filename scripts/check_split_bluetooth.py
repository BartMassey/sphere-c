#!/usr/bin/env python3
from spherosphere.paths import socket_path
"""Verify USB discovery concurrently with motherboard Mesh control."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from dbus_fast import Message, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, MessageType
from spherosphere.api import request


async def check(address, seconds, record):
    result = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
              "usb_address": address, "seconds": seconds, "samples": [],
              "discovered": [], "errors": [], "success": False}
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    adapter = None
    started = False
    observed = {}

    async def call(path, interface, member, signature="", body=None):
        reply = await asyncio.wait_for(bus.call(Message(
            destination="org.bluez", path=path, interface=interface,
            member=member, signature=signature, body=body or [],
        )), 15)
        if reply.message_type != MessageType.METHOD_RETURN:
            raise RuntimeError(reply.error_name)
        return reply.body

    def signal(message):
        path, props = None, None
        if message.message_type != MessageType.SIGNAL:
            return
        if message.interface == "org.freedesktop.DBus.ObjectManager" and message.member == "InterfacesAdded":
            path, interfaces = message.body
            props = interfaces.get("org.bluez.Device1")
        elif (message.interface == "org.freedesktop.DBus.Properties"
              and message.member == "PropertiesChanged"
              and message.body[0] == "org.bluez.Device1"):
            path, props = message.path, message.body[1]
        if not path or props is None or not path.startswith(adapter + "/dev_"):
            return
        if not any(key in props for key in ("RSSI", "ManufacturerData", "ServiceData")):
            return
        device = path.rsplit("dev_", 1)[1].replace("_", ":")
        item = observed.setdefault(device, {"address": device})
        for key in ("Name", "RSSI"):
            if key in props:
                item[key.lower()] = props[key].value

    try:
        objects, = await call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        matches = [path for path, interfaces in objects.items()
                   if "org.bluez.Adapter1" in interfaces
                   and interfaces["org.bluez.Adapter1"]["Address"].value.upper() == address.upper()]
        if len(matches) != 1:
            raise RuntimeError("USB controller not uniquely available")
        adapter = matches[0]
        result["usb_adapter_path"] = adapter
        props = objects[adapter]["org.bluez.Adapter1"]
        result["usb_powered"] = props["Powered"].value
        if not result["usb_powered"]:
            raise RuntimeError("USB controller is not powered")
        health = await request(socket_path(), "health")
        result["mesh_health_before"] = health
        if not health.get("ok") or not any(
                link.get("bearer") == "advertising" and link.get("relay")
                for link in health["result"].get("links", [])):
            raise RuntimeError("Advertising Mesh backend is not ready")
        bus.add_message_handler(signal)
        for rule in (
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesAdded'",
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',path_namespace='" + adapter + "'",
        ):
            reply = await bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule]))
            if reply.message_type != MessageType.METHOD_RETURN:
                raise RuntimeError("Could not subscribe to discovery")
        await call(adapter, "org.bluez.Adapter1", "SetDiscoveryFilter", "a{sv}",
                   [{"Transport": Variant("s", "auto"), "DuplicateData": Variant("b", False)}])
        await call(adapter, "org.bluez.Adapter1", "StartDiscovery")
        started = True
        result["discovery_started"] = True
        print(json.dumps({"event": "usb_discovery_started", "adapter": adapter}), flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            sample = {}
            for name in ("ARI", "BEN"):
                reply = await request(socket_path(), "control",
                                      {"device": name, "command": "read"})
                if not reply.get("ok"):
                    raise RuntimeError("Mesh status read failed")
                sample[name] = {"device": reply["result"]["device"],
                                "power": reply["result"]["state"]["present"]}
            result["samples"].append(sample)
            result["discovered"] = list(observed.values())
            record(result)
            print(json.dumps({"event": "concurrent_mesh_reads", "sample": len(result["samples"]),
                              "devices_seen": len(observed)}), flush=True)
            await asyncio.sleep(min(3, max(0, deadline - time.monotonic())))
        discovering, = await call(adapter, "org.freedesktop.DBus.Properties", "Get", "ss",
                                  ["org.bluez.Adapter1", "Discovering"])
        result["discovering_at_end"] = discovering.value
        if not result["discovering_at_end"]:
            raise RuntimeError("USB discovery stopped unexpectedly")
        result["success"] = bool(observed and result["samples"])
    except Exception as exc:
        result["errors"].append(type(exc).__name__)
    finally:
        if started:
            try:
                await call(adapter, "org.bluez.Adapter1", "StopDiscovery")
                result["discovery_stopped"] = True
            except Exception as exc:
                result["errors"].append(type(exc).__name__)
                result["success"] = False
        result["discovered"] = list(observed.values())
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        bus.disconnect()
        record(result)
    print(json.dumps({"event": "result", "success": result["success"],
                      "paired_mesh_samples": len(result["samples"]),
                      "devices_seen": len(observed), "errors": result["errors"]}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="B0:82:E2:2B:FD:35")
    parser.add_argument("--seconds", type=int, choices=range(5, 121), default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with open(args.output, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        def record(value):
            stream.seek(0)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        result = asyncio.run(check(args.address, args.seconds, record))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
