#!/usr/bin/env python3

import argparse
import re
from pathlib import Path

from sphere_mesh.capture import k2, k3


HEX_128 = re.compile(
    rb"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("old/phone_external/files"),
    )
    parser.add_argument(
        "--network-id",
        default="1d497fd760450169",
    )
    parser.add_argument("--nid", type=lambda value: int(value, 0), default=0x30)
    args = parser.parse_args()

    candidates: set[bytes] = set()
    scanned = 0
    unreadable = 0

    for path in args.root.rglob("*"):
        if not path.is_file():
            continue
        scanned += 1
        try:
            data = path.read_bytes()
        except OSError:
            unreadable += 1
            continue
        candidates.update(value.lower() for value in HEX_128.findall(data))

    matches = []
    for raw in sorted(candidates):
        key = bytes.fromhex(raw.decode())
        if (
            k3(key).hex() == args.network_id.lower()
            and k2(key, b"\x00")[0] == args.nid
        ):
            matches.append(raw.decode())

    print(f"files_scanned={scanned}")
    print(f"files_unreadable={unreadable}")
    print(f"unique_128bit_hex_candidates={len(candidates)}")
    print(f"validated_netkeys={len(matches)}")
    for match in matches:
        print(match)


if __name__ == "__main__":
    main()
