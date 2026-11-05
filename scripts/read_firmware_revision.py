#!/usr/bin/env python3
"""Read a Sphere's Device Information revision without provisioning.

Only GATT discovery and a characteristic read are performed. This tool
does not subscribe, write characteristics, pair, provision, or start OTA.
The binary field interpretation is inferred from the captured firmware;
raw bytes are retained so the version can be checked independently.
BlueZ's accumulated service metadata is not proof of pairing mode.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from bleak import BleakClient, BleakScanner


PROVISIONING = "00001827-0000-1000-8000-00805f9b34fb"
DEVICE_INFORMATION = "0000180a-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"


def interpret_revision(raw: bytes) -> dict:
    """Preserve the revision record and distinguish inferred text from bytes."""
    result = {"raw_hex": raw.hex(), "length": len(raw)}
    text = raw.rstrip(b"\0")
    if text and all(32 <= byte < 127 for byte in text):
        result["text"] = text.decode("ascii")
    if len(raw) == 10:
        result["revision_words_le"] = [
            f"0x{int.from_bytes(raw[start:start + 2], 'little'):04x}"
            for start in (2, 4)
        ]
        result["copies_agree"] = raw[2:4] == raw[4:6]
        if result["copies_agree"] and all(48 <= byte <= 57 for byte in raw[2:4]):
            result["version_candidate"] = ".".join(chr(byte) for byte in raw[2:4])
            result["interpretation"] = (
                "Captured-image initializer pairs ASCII bytes 05 with version "
                "string 0.5; dotted rendering of other values is inferred."
            )
    return result


async def read_revision(
    scan_seconds: float, address: str | None, *, scan_only: bool = False
) -> dict:
    """Select an observed device and read its revision; mode is unverified."""
    discovered = {}

    def detected(device, advertisement):
        discovered[device.address] = (device, advertisement)

    cleanup_warnings = []
    scanner = BleakScanner(detection_callback=detected)
    await scanner.start()
    try:
        await asyncio.sleep(scan_seconds)
        candidates = []
        for device, advertisement in discovered.values():
            services = {uuid.lower() for uuid in advertisement.service_uuids}
            services.update(uuid.lower() for uuid in advertisement.service_data)
            if address is not None:
                if device.address.lower() != address.lower():
                    continue
            elif PROVISIONING not in services:
                continue
            candidates.append((device, advertisement))
        if scan_only:
            return {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "operation": "advertisement metadata scan only; no connection",
                "observed": [
                    {
                        "address": device.address,
                        "name": device.name,
                        "rssi": advertisement.rssi,
                        "service_uuids": advertisement.service_uuids,
                        "service_data": {
                            uuid: bytes(value).hex()
                            for uuid, value in advertisement.service_data.items()
                        },
                    }
                    for device, advertisement in candidates
                ],
                "cleanup_warnings": cleanup_warnings,
            }
        if len(candidates) != 1:
            found = ", ".join(device.address for device, _ in candidates) or "none"
            raise RuntimeError(
                f"Expected one matching observed device; found {len(candidates)}: {found}. "
                "Use --address to disambiguate; no connection was attempted."
            )
        device, advertisement = candidates[0]
        print(f"Connecting to {device.address} ({device.name})", file=sys.stderr, flush=True)
        client = BleakClient(device, timeout=20.0)
        await client.connect()
        try:
            matches = [
                characteristic
                for service in client.services
                if service.uuid.lower() == DEVICE_INFORMATION
                for characteristic in service.characteristics
                if characteristic.uuid.lower() == FIRMWARE_REVISION
            ]
            if len(matches) != 1 or "read" not in matches[0].properties:
                raise RuntimeError("Expected one readable Firmware Revision characteristic.")
            characteristic = matches[0]
            print(f"Reading revision at handle {characteristic.handle}", file=sys.stderr, flush=True)
            raw = bytes(await client.read_gatt_char(characteristic))
            return {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "address": device.address,
                "name": device.name,
                "rssi": advertisement.rssi,
                "service": DEVICE_INFORMATION,
                "characteristic": FIRMWARE_REVISION,
                "handle": characteristic.handle,
                "operation": "direct GATT read; no provisioning or OTA",
                "pairing_mode_verified": False,
                "revision": interpret_revision(raw),
                "cleanup_warnings": cleanup_warnings,
            }
        finally:
            try:
                await client.disconnect()
            except Exception as exc:
                cleanup_warnings.append(f"disconnect: {exc}")
    finally:
        try:
            await scanner.stop()
        except Exception as exc:
            cleanup_warnings.append(f"scanner stop: {exc}")
            print(f"Scanner cleanup warning: {exc}", file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-seconds", type=float, default=12.0)
    parser.add_argument("--address", help="Optional exact observed device address")
    parser.add_argument("--scan-only", action="store_true", help="Save advertisement metadata without connecting")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.scan_seconds <= 60:
        parser.error("--scan-seconds must be greater than zero and at most 60")
    if args.output.exists():
        parser.error("--output already exists; choose a new capture path")
    result = asyncio.run(read_revision(args.scan_seconds, args.address, scan_only=args.scan_only))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result, indent=2))
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
