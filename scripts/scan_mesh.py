#!/usr/bin/env python3

import argparse
import asyncio
import json

from bleak import BleakScanner


RELEVANT_UUIDS = ("1827", "1828", "fcf1", "fd50")


def short_uuid(value: str) -> str | None:
    normalized = value.lower().replace("-", "")
    for candidate in RELEVANT_UUIDS:
        if normalized.startswith(f"0000{candidate}"):
            return candidate
    return None


async def scan(duration: float) -> None:
    discovered = await BleakScanner.discover(
        timeout=duration,
        return_adv=True,
    )
    rows = []
    for device, advertisement in discovered.values():
        services = {
            candidate
            for value in advertisement.service_uuids
            if (candidate := short_uuid(value)) is not None
        }
        service_data = {}
        for key, value in advertisement.service_data.items():
            candidate = short_uuid(key)
            if candidate is not None:
                services.add(candidate)
                service_data[candidate] = value.hex()
        manufacturer_data = {
            f"0x{company:04x}": value.hex()
            for company, value in advertisement.manufacturer_data.items()
            if company == 0x07D0
        }
        if not services and not manufacturer_data:
            continue
        rows.append(
            {
                "address": device.address,
                "name": device.name,
                "rssi": advertisement.rssi,
                "services": sorted(services),
                "service_data": service_data,
                "manufacturer_data": manufacturer_data,
            }
        )
    rows.sort(key=lambda row: row["rssi"], reverse=True)
    print(json.dumps(rows, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()
    asyncio.run(scan(args.seconds))


if __name__ == "__main__":
    main()
