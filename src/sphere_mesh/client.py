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
    effect = commands.add_parser("effect", help="list, read, or set a built-in effect")
    effect_commands = effect.add_subparsers(dest="operation", required=True)
    effect_commands.add_parser("list", help="list effect names and labels")
    effect_commands.add_parser("get", help="read the current vendor snapshot")
    for operation, help_text in (
        ("set", "select a visible effect with DP 102"),
        ("select", "select a visible effect with DP 102"),
        ("activate", "enter colour mode, then select a visible effect"),
    ):
        effect_command = effect_commands.add_parser(operation, help=help_text)
        effect_command.add_argument(
            "name", help="effect name, such as flame or colour_2"
        )
    countdown = commands.add_parser(
        "countdown", help="read, set, or cancel the panel countdown"
    )
    countdown_commands = countdown.add_subparsers(dest="operation", required=True)
    countdown_commands.add_parser("get", help="read remaining countdown seconds")
    countdown_commands.add_parser("cancel", help="set countdown to zero")
    countdown_set = countdown_commands.add_parser(
        "set", help="set countdown seconds"
    )
    countdown_set.add_argument("seconds", type=int, metavar="SECONDS")
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "health":
        method, params = "health", {}
    elif args.command == "control":
        method, params = "control", {"command": args.operation}
    elif args.command == "vendor-read":
        method, params = "vendor.read", {"dp_ids": args.dp_ids}
    elif args.command == "effect":
        method = f"effect.{args.operation}"
        params = (
            {"name": args.name}
            if args.operation in ("set", "select", "activate")
            else {}
        )
    elif args.command == "countdown":
        method = f"countdown.{args.operation}"
        params = {"seconds": args.seconds} if args.operation == "set" else {}
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
