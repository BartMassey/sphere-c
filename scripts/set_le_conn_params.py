"""Load per-device BLE connection parameters through Linux MGMT."""

import argparse
import socket
import struct


MGMT_OP_LOAD_CONN_PARAM = 0x0035
MGMT_EV_CMD_COMPLETE = 0x0001
MGMT_EV_CMD_STATUS = 0x0002
MGMT_ADDR_LE_PUBLIC = 0x01


def parse_address(value: str) -> bytes:
    parts = value.split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("address must contain six hex octets")
    try:
        return bytes(int(part, 16) for part in reversed(parts))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("address contains invalid hex") from exc


def load_parameters(
    index: int,
    address: bytes,
    min_interval: int,
    max_interval: int,
    latency: int,
    timeout: int,
) -> None:
    entry = struct.pack(
        "<6sBHHHH",
        address,
        MGMT_ADDR_LE_PUBLIC,
        min_interval,
        max_interval,
        latency,
        timeout,
    )
    payload = struct.pack("<H", 1) + entry
    packet = struct.pack(
        "<HHH", MGMT_OP_LOAD_CONN_PARAM, index, len(payload)
    ) + payload

    with socket.socket(
        socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI
    ) as control:
        control.bind((socket.HCI_DEV_NONE, socket.HCI_CHANNEL_CONTROL))
        control.send(packet)
        while True:
            reply = control.recv(1024)
            event, reply_index, length = struct.unpack_from("<HHH", reply)
            body = reply[6 : 6 + length]
            if reply_index != index:
                continue
            if event == MGMT_EV_CMD_COMPLETE:
                opcode, status = struct.unpack_from("<HB", body)
            elif event == MGMT_EV_CMD_STATUS:
                status, opcode = struct.unpack_from("<BH", body)
            else:
                continue
            if opcode != MGMT_OP_LOAD_CONN_PARAM:
                continue
            if status:
                raise RuntimeError(f"MGMT rejected parameters: status 0x{status:02x}")
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("address", type=parse_address)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--min-interval", type=int, default=8)
    parser.add_argument("--max-interval", type=int, default=16)
    parser.add_argument("--latency", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=500)
    args = parser.parse_args()
    load_parameters(
        args.index,
        args.address,
        args.min_interval,
        args.max_interval,
        args.latency,
        args.timeout,
    )
    print(
        "Loaded LE parameters: interval "
        f"{args.min_interval * 1.25:g}-{args.max_interval * 1.25:g} ms, "
        f"latency {args.latency}, timeout {args.timeout * 10} ms"
    )


if __name__ == "__main__":
    main()
