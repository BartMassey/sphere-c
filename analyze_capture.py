#!/usr/bin/env python3

import argparse
import csv
import io
import subprocess
from collections import Counter
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.cmac import CMAC


OPCODES = {
    bytes.fromhex("8201"): "Generic OnOff Get",
    bytes.fromhex("8202"): "Generic OnOff Set",
    bytes.fromhex("8203"): "Generic OnOff Set Unacknowledged",
    bytes.fromhex("8204"): "Generic OnOff Status",
    bytes.fromhex("8205"): "Generic Level Get",
    bytes.fromhex("8206"): "Generic Level Set",
    bytes.fromhex("8207"): "Generic Level Set Unacknowledged",
    bytes.fromhex("8208"): "Generic Level Status",
    bytes.fromhex("824b"): "Light Lightness Get",
    bytes.fromhex("824c"): "Light Lightness Set",
    bytes.fromhex("824d"): "Light Lightness Set Unacknowledged",
    bytes.fromhex("824e"): "Light Lightness Status",
    bytes.fromhex("825d"): "Light CTL Get",
    bytes.fromhex("825e"): "Light CTL Set",
    bytes.fromhex("825f"): "Light CTL Set Unacknowledged",
    bytes.fromhex("8260"): "Light CTL Status",
}


def run_tshark(capture, display_filter, fields):
    command = [
        "tshark",
        "-r",
        str(capture),
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-E",
        "header=y",
        "-E",
        "separator=\t",
        "-E",
        "quote=n",
        "-E",
        "occurrence=f",
    ]
    for field in fields:
        command.extend(["-e", field])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return list(csv.DictReader(io.StringIO(result.stdout), delimiter="\t"))


def cmac(key, data):
    value = CMAC(algorithms.AES(key))
    value.update(data)
    return value.finalize()


def s1(data):
    return cmac(bytes(16), data)


def k1(key, salt, info):
    return cmac(cmac(salt, key), info)


def k2(net_key, parameter=b"\x00"):
    salt = s1(b"smk2")
    temporary = cmac(salt, net_key)
    t1 = cmac(temporary, parameter + b"\x01")
    t2 = cmac(temporary, t1 + parameter + b"\x02")
    t3 = cmac(temporary, t2 + parameter + b"\x03")
    return t1[-1] & 0x7f, t2, t3


def k3(net_key):
    salt = s1(b"smk3")
    temporary = cmac(salt, net_key)
    return cmac(temporary, b"id64\x01")[-8:]


def k4(app_key):
    salt = s1(b"smk4")
    temporary = cmac(salt, app_key)
    return cmac(temporary, b"id6\x01")[-1] & 0x3f


def parse_key(value, name):
    if value is None:
        return None
    try:
        key = bytes.fromhex(value.replace(":", "").replace(" ", ""))
    except ValueError as error:
        raise SystemExit(f"{name} must be hexadecimal") from error
    if len(key) != 16:
        raise SystemExit(f"{name} must be exactly 16 bytes")
    return key


def decrypt_network(pdu, net_key, iv_index):
    nid, encryption_key, privacy_key = k2(net_key)
    if pdu[0] & 0x7f != nid:
        raise ValueError(f"NID mismatch: packet 0x{pdu[0] & 0x7f:02x}, key 0x{nid:02x}")
    packet_ivi = pdu[0] >> 7
    candidates = [iv_index]
    if iv_index > 0:
        candidates.append(iv_index - 1)
    selected_iv = next((item for item in candidates if item & 1 == packet_ivi), None)
    if selected_iv is None:
        raise ValueError("IVI does not match the supplied IV Index")
    obfuscated = pdu[1:7]
    encrypted = pdu[7:]
    privacy_plaintext = bytes(5) + selected_iv.to_bytes(4, "big") + encrypted[:7]
    cipher = Cipher(algorithms.AES(privacy_key), modes.ECB()).encryptor()
    privacy_mask = cipher.update(privacy_plaintext) + cipher.finalize()
    clear_header = bytes(left ^ right for left, right in zip(obfuscated, privacy_mask[:6]))
    ctl_ttl = clear_header[0]
    sequence = int.from_bytes(clear_header[1:4], "big")
    source = int.from_bytes(clear_header[4:6], "big")
    mic_length = 8 if ctl_ttl & 0x80 else 4
    nonce = (
        b"\x00"
        + bytes([ctl_ttl])
        + sequence.to_bytes(3, "big")
        + source.to_bytes(2, "big")
        + bytes(2)
        + selected_iv.to_bytes(4, "big")
    )
    plaintext = AESCCM(encryption_key, tag_length=mic_length).decrypt(
        nonce, encrypted, None
    )
    return {
        "ctl": ctl_ttl >> 7,
        "ttl": ctl_ttl & 0x7f,
        "sequence": sequence,
        "source": source,
        "destination": int.from_bytes(plaintext[:2], "big"),
        "transport": plaintext[2:],
        "iv_index": selected_iv,
    }


def opcode_info(access):
    if not access:
        return "empty access payload"
    first = access[0]
    if first & 0x80 == 0:
        opcode = access[:1]
        length = 1
    elif first & 0xc0 == 0x80:
        opcode = access[:2]
        length = 2
    else:
        opcode = access[:3]
        length = 3
    name = OPCODES.get(opcode)
    if name is None and length == 3:
        company = int.from_bytes(opcode[1:3], "little")
        name = f"vendor opcode 0x{opcode[0] & 0x3f:02x}, company 0x{company:04x}"
    if name is None:
        name = f"opcode {opcode.hex()}"
    return f"{name}; parameters={access[length:].hex() or '-'}"


def decrypt_access(network, app_key):
    transport = network["transport"]
    if network["ctl"]:
        return "control message"
    if not transport:
        return "empty transport PDU"
    header = transport[0]
    if header & 0x80:
        return "segmented access message"
    if header & 0x40 == 0:
        return "device-key access message"
    aid = header & 0x3f
    expected_aid = k4(app_key)
    if aid != expected_aid:
        return f"AppKey AID mismatch: packet 0x{aid:02x}, key 0x{expected_aid:02x}"
    nonce = (
        b"\x01\x00"
        + network["sequence"].to_bytes(3, "big")
        + network["source"].to_bytes(2, "big")
        + network["destination"].to_bytes(2, "big")
        + network["iv_index"].to_bytes(4, "big")
    )
    try:
        access = AESCCM(app_key, tag_length=4).decrypt(nonce, transport[1:], None)
    except Exception:
        return "AppKey authentication failed"
    return opcode_info(access)


def get_beacons(capture):
    fields = [
        "frame.number",
        "frame.time_relative",
        "beacon.flags",
        "beacon.network_id",
        "beacon.ivindex",
        "beacon.authentication_value",
    ]
    return run_tshark(capture, "beacon.type == 1", fields)


def get_fcf1_advertisements(capture):
    fields = [
        "frame.number",
        "frame.time_relative",
        "bthci_evt.bd_addr",
        "bthci_evt.le_rssi",
        "btcommon.eir_ad.entry.service_data",
    ]
    return run_tshark(
        capture, "btcommon.eir_ad.entry.uuid_16 == 0xfcf1", fields
    )


def get_pdus(capture):
    fields = [
        "frame.number",
        "frame.time_relative",
        "hci_h4.direction",
        "btatt.opcode",
        "btatt.handle",
        "btmproxy.sar",
        "btmproxy.type",
        "btmesh.ivi",
        "btmesh.nid",
        "btmesh.obfuscated",
        "btmesh.encrypted",
    ]
    rows = run_tshark(capture, "btmproxy.type == 0", fields)
    parsed = []
    for row in rows:
        pdu = bytes([(int(row["btmesh.ivi"]) << 7) | int(row["btmesh.nid"])])
        pdu += bytes.fromhex(row["btmesh.obfuscated"] + row["btmesh.encrypted"])
        parsed.append(
            {
                "frame": int(row["frame.number"]),
                "time": float(row["frame.time_relative"]),
                "direction": "phone->proxy"
                if row["hci_h4.direction"] == "0x00"
                else "proxy->phone",
                "att_opcode": row["btatt.opcode"],
                "handle": row["btatt.handle"],
                "sar": int(row["btmproxy.sar"]),
                "pdu": pdu,
            }
        )
    return parsed


def print_summary(capture, beacons, advertisements, pdus):
    print(f"Capture: {capture}")
    print(f"Network PDUs through Mesh Proxy: {len(pdus)}")
    for direction in ("phone->proxy", "proxy->phone"):
        selected = [item for item in pdus if item["direction"] == direction]
        lengths = Counter(len(item["pdu"]) for item in selected)
        rendered = ", ".join(f"{length}:{count}" for length, count in sorted(lengths.items()))
        print(f"  {direction}: {len(selected)} ({rendered})")
    unique_beacons = {
        (row["beacon.network_id"], row["beacon.ivindex"], row["beacon.flags"])
        for row in beacons
    }
    print(f"Secure Network Beacons: {len(beacons)} ({len(unique_beacons)} unique states)")
    for network_id, iv_index, flags in sorted(unique_beacons):
        print(f"  network_id={network_id} iv_index={iv_index} flags={flags}")
    identities = {}
    for row in advertisements:
        payload = row["btcommon.eir_ad.entry.service_data"]
        identity = identities.setdefault(payload, {"addresses": set(), "times": []})
        identity["addresses"].add(row["bthci_evt.bd_addr"])
        identity["times"].append(float(row["frame.time_relative"]))
    print(f"FCF1 advertisements: {len(advertisements)} ({len(identities)} stable payloads)")
    for payload, identity in sorted(identities.items()):
        addresses = ",".join(sorted(identity["addresses"]))
        print(
            f"  bytes={len(bytes.fromhex(payload)):2d} count={len(identity['times']):3d} "
            f"time={min(identity['times']):.3f}-{max(identity['times']):.3f} "
            f"addresses={addresses} data={payload}"
        )


def print_bursts(pdus, gap):
    bursts = []
    current = []
    for pdu in pdus:
        if current and pdu["time"] - current[-1]["time"] > gap:
            bursts.append(current)
            current = []
        current.append(pdu)
    if current:
        bursts.append(current)
    print(f"\nActivity bursts separated by more than {gap:g} seconds:")
    for index, burst in enumerate(bursts, 1):
        sent = sum(item["direction"] == "phone->proxy" for item in burst)
        received = len(burst) - sent
        print(
            f"  {index:02d}: {burst[0]['time']:9.3f}-{burst[-1]['time']:9.3f} "
            f"frames {burst[0]['frame']}-{burst[-1]['frame']} tx={sent} rx={received}"
        )


def print_pdus(pdus, net_key, app_key, iv_index):
    print("\nProxy Network PDUs:")
    for item in pdus:
        detail = ""
        if net_key is not None:
            try:
                network = decrypt_network(item["pdu"], net_key, iv_index)
                detail = (
                    f" ctl={network['ctl']} ttl={network['ttl']} seq={network['sequence']}"
                    f" src=0x{network['source']:04x} dst=0x{network['destination']:04x}"
                    f" lower={network['transport'].hex()}"
                )
                if app_key is not None:
                    detail += f" access=({decrypt_access(network, app_key)})"
            except Exception as error:
                detail = f" decrypt_error=({error})"
        print(
            f"  {item['frame']:4d} {item['time']:10.6f} {item['direction']:12s} "
            f"len={len(item['pdu']):2d} {item['pdu'].hex()}{detail}"
        )


def write_csv(path, pdus):
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["frame", "time_relative", "direction", "pdu_length", "network_pdu"])
        for item in pdus:
            writer.writerow(
                [
                    item["frame"],
                    f"{item['time']:.6f}",
                    item["direction"],
                    len(item["pdu"]),
                    item["pdu"].hex(),
                ]
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path, nargs="?", default=Path("sphere-c.btsnoop"))
    parser.add_argument("--net-key")
    parser.add_argument("--app-key")
    parser.add_argument("--iv-index", type=lambda value: int(value, 0))
    parser.add_argument("--gap", type=float, default=1.0)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--no-pdus", action="store_true")
    arguments = parser.parse_args()

    net_key = parse_key(arguments.net_key, "NetKey")
    app_key = parse_key(arguments.app_key, "AppKey")
    beacons = get_beacons(arguments.capture)
    advertisements = get_fcf1_advertisements(arguments.capture)
    pdus = get_pdus(arguments.capture)
    observed_iv = int(beacons[0]["beacon.ivindex"]) if beacons else 0
    iv_index = arguments.iv_index if arguments.iv_index is not None else observed_iv

    print_summary(arguments.capture, beacons, advertisements, pdus)
    if net_key is not None:
        network_id = k3(net_key).hex()
        observed_ids = {row["beacon.network_id"] for row in beacons}
        verdict = "matches capture" if network_id in observed_ids else "DOES NOT match capture"
        print(f"Supplied NetKey derives Network ID {network_id}: {verdict}")
    print_bursts(pdus, arguments.gap)
    if not arguments.no_pdus:
        print_pdus(pdus, net_key, app_key, iv_index)
    if arguments.csv is not None:
        write_csv(arguments.csv, pdus)


if __name__ == "__main__":
    main()
