"""Command-line client for the local Sphere service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .api import ApiProtocolError, request


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control the local Sphere service")
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path("state/sphere.sock"),
        help="service socket (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="show service and Bluetooth health")
    basic = commands.add_parser("control", help="run a basic power command")
    basic.add_argument("operation", choices=("read", "on", "off", "toggle"))
    lighting = commands.add_parser("lighting", help="read or set lighting state")
    lighting.add_argument("operation")
    vendor = commands.add_parser(
        "vendor-read", help="read Tuya/Olight vendor datapoints"
    )
    vendor.add_argument(
        "dp_ids",
        nargs="+",
        type=lambda value: int(value, 0),
        metavar="DP_ID",
    )
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "health":
        method, params = "health", {}
    elif args.command == "control":
        method, params = "control", {"command": args.operation}
    elif args.command == "vendor-read":
        method, params = "vendor.read", {"dp_ids": args.dp_ids}
    else:
        method, params = "lighting", {"command": args.operation}
    try:
        response = await request(args.socket, method, params)
    except (OSError, ApiProtocolError) as exc:
        print(f"sphere service unavailable: {exc}", file=sys.stderr)
        return 1
    output = response.get("result") if response["ok"] else response.get("error")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if response["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
