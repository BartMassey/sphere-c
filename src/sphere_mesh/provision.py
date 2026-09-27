"""Provision and inspect a Sphere C over Bluetooth Mesh PB-GATT.

Milestones (the go/no-go evidence):
  A — provisioning completes over PB-GATT (device key derived, netkey placed)
  B — Generic OnOff round-trips through the GATT proxy (GO)

Every frame on the air is hex-dumped at DEBUG level into provision_run.log
(and to the console with --verbose). State (keys, addresses, SEQ) persists in
a JSON file; SEQ is saved after every network PDU send because a replayed SEQ
after a crash would be silently ignored by the node.

Known Phase 0 limitations: strict IV Index match (no IV-update recovery), no
reliable retransmission of missing outbound segments, no replay cache.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from btmesh.access import (
    MODEL_GENERIC_ONOFF_SERVER,
    MODEL_LIGHT_LIGHTNESS_SERVER,
    OP_CONFIG_APPKEY_STATUS,
    OP_CONFIG_COMPOSITION_DATA_STATUS,
    OP_CONFIG_MODEL_APP_STATUS,
    OP_GENERIC_ONOFF_STATUS,
    OP_LIGHT_LIGHTNESS_STATUS,
    OP_NODE_RESET_STATUS,
    STATUS_NAMES,
    config_appkey_add,
    config_composition_data_get,
    config_model_app_bind,
    config_model_app_bind_vendor,
    config_node_reset,
    encode_opcode,
    generic_onoff_get,
    generic_onoff_set,
    light_lightness_get,
    light_lightness_set,
    parse_composition_data_status,
    parse_config_appkey_status,
    parse_config_model_app_status,
    parse_generic_onoff_status,
    parse_light_lightness_status,
)
from btmesh.bearer import (
    IDENTIFICATION_NETWORK_ID,
    BearerError,
    EsphomeTransport,
)
from btmesh.crypto import k3
from btmesh.errors import BtMeshError
from btmesh.node import MeshNode, ReceivedMessage
from btmesh.provisioner import Provisioner
from btmesh.proxy_pdu import MSG_TYPE_PROVISIONING_PDU
from btmesh.pump import BearerPump
from .access import (
    OP_CONFIG_NETWORK_TRANSMIT_STATUS,
    config_network_transmit_set,
)
from .transport import (
    LocalTransport,
    disconnect_quietly,
    find_proxy_node,
    open_gatt_session,
    open_mesh_proxy_session,
    scan_unprovisioned,
)
from .state import (
    StateError,
    StateLockedError,
    StateStore,
    load_or_create_state,
    save_state,
)

logger = logging.getLogger("sphere-provision")

LOG_FILE = "state/provision_run.log"
SCAN_UNPROVISIONED_S = float(os.environ.get("SPHERE_SCAN_SECONDS", "60"))
SCAN_PROXY_S = 5.0
PROVISIONING_TIMEOUT_S = 60.0

LIMITATIONS_NOTE = (
    "Known Phase 0 limitations: strict IV Index match (no IV-update recovery); "
    "no reliable retransmission of missing outbound segments; no replay cache."
)

CONFIG_TIMEOUT_CAUSES = [
    "wrong device key or unicast address (state file out of sync with the node "
    "— factory-reset the node and delete the state file to start over)",
    "IV Index mismatch (strict match required in Phase 0)",
    "SEQ replay: if the state file was deleted or rolled back, the node "
    "silently drops our PDUs — bump \"seq\" in the state file well past any "
    "previously used value",
    "proxy output filter dropped the response (whitelist should auto-include "
    "our source address, but a vendor stack may differ)",
    "a segmented response was lost (we never ack segments; the node retries, "
    "but long responses can still time out)",
]


class Phase0Failure(Exception):
    """A phase failed with a known diagnosis; carries the triage causes."""

    def __init__(self, phase: str, message: str, causes: list[str]) -> None:
        super().__init__(message)
        self.phase = phase
        self.causes = causes


# ----------------------------------------------------------------- plumbing


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision, inspect, configure, and optionally control a "
        "Sphere C over Bluetooth Mesh PB-GATT.",
    )
    parser.add_argument(
        "--transport", choices=("esphome", "local"), default="local",
        help="BLE path: 'esphome' via an ESPHome Bluetooth proxy, 'local' via "
        "the PC's own adapter (default: %(default)s)",
    )
    parser.add_argument("--esphome-host", help="ESPHome proxy hostname/IP")
    parser.add_argument("--esphome-key", help="ESPHome API encryption (noise) PSK")
    parser.add_argument(
        "--device-uuid",
        help="target Device UUID (32 hex chars); default: first 0x1827 beacon",
    )
    parser.add_argument(
        "--static-oob", metavar="HEX",
        help="static OOB authentication value (1-16 bytes, hex)",
    )
    parser.add_argument(
        "--state", default="state/sphere_state.json",
        help="state file: keys, addresses, SEQ (default: %(default)s)",
    )
    parser.add_argument(
        "--init-only", action="store_true",
        help="create or validate the key state, then exit without using BLE",
    )
    parser.add_argument(
        "--provision-only", action="store_true",
        help="provision and save the device key, then stop before configuration",
    )
    parser.add_argument(
        "--toggle-only", action="store_true",
        help="skip provisioning and config; use the device from the state file",
    )
    parser.add_argument(
        "--no-toggle", action="store_true",
        help="configure and inspect the node, but do not change the light state",
    )
    parser.add_argument(
        "--reset-node", action="store_true",
        help="send a device-keyed Config Node Reset and perform no other action",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="DEBUG logging (frame hex dumps) on the console as well",
    )
    args = parser.parse_args(argv)

    if args.transport == "esphome" and not args.esphome_host:
        parser.error("--esphome-host is required with --transport esphome")
    if args.init_only and (args.provision_only or args.toggle_only or args.reset_node):
        parser.error("--init-only cannot be combined with a run mode")
    if sum((args.provision_only, args.toggle_only, args.reset_node)) > 1:
        parser.error("provision, reuse, and reset modes are mutually exclusive")
    if args.device_uuid:
        args.device_uuid = args.device_uuid.replace("-", "").lower()
        try:
            if len(bytes.fromhex(args.device_uuid)) != 16:
                raise ValueError
        except ValueError:
            parser.error("--device-uuid must be 16 bytes of hex")
    if args.static_oob is not None:  # explicit "" must error, not mean no-OOB
        try:
            if not 1 <= len(bytes.fromhex(args.static_oob)) <= 16:
                raise ValueError
        except ValueError:
            parser.error("--static-oob must be 1-16 bytes of hex")
    return args


def setup_logging(verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname).1s %(name)s: %(message)s"))
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfile = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(
        logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s")
    )
    root.addHandler(console)
    root.addHandler(logfile)
    # Third-party DEBUG floods the interop log; keep it to the essentials.
    for noisy in ("aioesphomeapi", "habluetooth", "bleak", "bleak_esphome"):
        logging.getLogger(noisy).setLevel(logging.INFO if verbose else logging.WARNING)
    logger.info("---- provision_and_toggle run started ----")


def print_diagnostic(phase: str, exc: BaseException, causes: list[str]) -> None:
    print()
    print("=" * 64)
    print(f"DIAGNOSTIC — phase failed: {phase}")
    print(f"Error: {exc}")
    if causes:
        print("Likely causes / next steps:")
        for cause in causes:
            print(f"  - {cause}")
    print(LIMITATIONS_NOTE)
    print(f"Frame-level hex dumps: {LOG_FILE}")
    print("=" * 64)
    logger.error("phase %r failed: %s", phase, exc)


def _full_payload(msg: ReceivedMessage) -> bytes:
    """Rebuild the on-air access payload for the access.parse_* helpers."""
    return encode_opcode(msg.opcode) + msg.params


def _timeout_causes(pump: BearerPump, extra: list[str] | None = None) -> list[str]:
    """Triage list for a request timeout, truthful about a dead TX pump.

    If the pump died, nothing was actually transmitted — that fact must come
    FIRST, before the on-air explanations, or the diagnostic lies.
    """
    causes = list(CONFIG_TIMEOUT_CAUSES) + (extra or [])
    if pump.failure is not None:
        causes.insert(
            0,
            f"bearer TX pump died: {pump.failure} — the request was never "
            "transmitted; the causes below do not apply",
        )
    return causes


# ------------------------------------------------------- provisioning phase


def _provisioning_causes(message: str) -> list[str]:
    causes = []
    if "OOB" in message:
        causes += [
            "the device demands OOB authentication this provisioner cannot do "
            "(output/input OOB) — see the 'capabilities:' line logged above",
            "if it lists static OOB, rerun with --static-oob HEX (value from "
            "the vendor app/label)",
        ]
    if "confirmation does not match" in message:
        causes += [
            "wrong --static-oob value (AuthValue mismatch)",
            "device expected a different auth method",
        ]
    causes += [
        "the Provisioner instance is dead after any error — simply rerun the "
        "script for a fresh session",
    ]
    return causes


async def provision(args, state: dict, state_path: Path, transport) -> int:
    """Scan, connect PB-GATT, drive the provisioning handshake. Milestone A."""
    netkey = bytes.fromhex(state["netkey"])
    scanner = transport.scanner()
    print(f"Scanning {SCAN_UNPROVISIONED_S:.0f} s for unprovisioned devices (0x1827)...")
    # Connect as soon as the (matching) beacon shows up: Telink lamps only
    # accept connections in a short window after power-up.
    beacons = await scan_unprovisioned(
        scanner,
        timeout=SCAN_UNPROVISIONED_S,
        stop_on=bytes.fromhex(args.device_uuid) if args.device_uuid else None,
        stop_on_any=not args.device_uuid,
        stop_after=False,
    )
    for b in beacons:
        print(
            f"  0x1827 beacon {b.device.address}: "
            f"Device UUID {b.uuid.hex()}, OOB info 0x{b.oob_info:04x}"
        )
    if not beacons:
        await scanner.stop()
        raise Phase0Failure(
            "provisioning scan",
            "no 0x1827 unprovisioned-device beacon seen",
            [
                "device is already provisioned — factory-reset it to restore "
                "the beacon (vendor app or power-cycle sequence)",
                "device supports only PB-ADV, not PB-GATT (out of Phase 0 scope)",
                "device unpowered or out of radio range of the proxy/adapter",
            ],
        )
    if args.device_uuid:
        want = bytes.fromhex(args.device_uuid)
        matching = [b for b in beacons if b.uuid == want]
        if not matching:
            await scanner.stop()
            raise Phase0Failure(
                "provisioning scan",
                f"no beacon with Device UUID {args.device_uuid}",
                ["check the UUID against the beacon list above"],
            )
        target = matching[0]
    else:
        target = beacons[0]
        if len(beacons) > 1:
            print(
                f"  multiple beacons; picking {target.uuid.hex()} "
                "(use --device-uuid to override)"
            )

    print(f"Connecting PB-GATT to {target.device.address}...")
    # The real handler is installed once the session objects exist below; the
    # lamp sends nothing before our Provisioning Invite, so nothing is lost.
    session_handler: list = []

    def dispatch(msg_type: int, payload: bytes) -> None:
        if session_handler:
            session_handler[0](msg_type, payload)
        else:
            logger.debug("PDU before session ready: 0x%02x %s", msg_type, payload.hex())

    try:
        client, bearer = await open_gatt_session(
            transport, target.device, provisioning=True, on_message=dispatch
        )
    finally:
        await scanner.stop()
    pump = BearerPump(bearer, MSG_TYPE_PROVISIONING_PDU)

    unicast = state["next_unicast"]
    static_oob = bytes.fromhex(args.static_oob) if args.static_oob else None
    prov = Provisioner(
        netkey,
        state["netkey_index"],
        state["iv_index"],
        unicast,
        send=pump.put,
        static_oob=static_oob,
    )

    done = asyncio.Event()
    errors: list[BaseException] = []

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        done.set()

    prov.on_done = done.set
    pump.on_error = fail

    def on_message(msg_type: int, payload: bytes) -> None:
        if msg_type != MSG_TYPE_PROVISIONING_PDU:
            logger.debug("ignoring message type 0x%02x during provisioning", msg_type)
            return
        try:
            prov.handle_pdu(payload)
        except BaseException as exc:  # session is dead; surface to the waiter
            fail(exc)

    try:
        session_handler.append(on_message)
        pump.start()
        prov.start()
        try:
            await asyncio.wait_for(done.wait(), timeout=PROVISIONING_TIMEOUT_S)
        except TimeoutError:
            raise Phase0Failure(
                "provisioning",
                f"no progress for {PROVISIONING_TIMEOUT_S:.0f} s "
                f"(state {prov.state.name})",
                [
                    "device stopped responding mid-handshake (link loss?)",
                    "device stack rejects our PDUs silently — check the hex "
                    "dumps for what was last sent/received",
                ],
            ) from None
        if errors:
            exc = errors[0]
            if isinstance(exc, BtMeshError):
                raise Phase0Failure(
                    "provisioning",
                    f"{exc} (state {prov.state.name})",
                    _provisioning_causes(str(exc)),
                ) from exc
            raise exc
    finally:
        await pump.stop()
        await bearer.stop()
        await disconnect_quietly(client)

    if not prov.done or prov.device_key is None:  # no assert: dies under -O
        raise Phase0Failure(
            "provisioning",
            f"session ended without Complete (state {prov.state.name})",
            ["internal error — check the hex dumps for the last exchange"],
        )
    print()
    print("=== MILESTONE A: PROVISIONING COMPLETE ===")
    print(f"  unicast address: 0x{unicast:04x}")
    print(f"  device key:      {prov.device_key.hex()}")
    print()
    state["devices"][f"0x{unicast:04x}"] = {
        "device_key": prov.device_key.hex(),
        "uuid": target.uuid.hex(),
        "address": target.device.address,
    }
    state["active_device"] = f"0x{unicast:04x}"
    # Advance past every element of the node so a future device in this state
    # file cannot overlap the lamp's secondary elements.
    state["next_unicast"] = unicast + max(1, prov.capabilities.num_elements)
    save_state(state_path, state)
    return unicast


# ------------------------------------------------------------- proxy phase


def pick_device(state: dict, args) -> int:
    """--toggle-only: select a previously provisioned device from the state."""
    if not state["devices"]:
        raise Phase0Failure(
            "startup",
            "--toggle-only, but the state file has no provisioned device",
            ["run once without --toggle-only to provision first"],
        )
    if args.device_uuid:
        matches = [
            addr_hex
            for addr_hex, entry in state["devices"].items()
            if entry["uuid"] == args.device_uuid
        ]
        active = state.get("active_device")
        if active in matches:
            return int(active, 16)
        if matches:
            return int(matches[-1], 16)
        raise Phase0Failure(
            "startup",
            f"no device with UUID {args.device_uuid} in the state file",
            ["check the state file's devices table"],
        )
    addr_hex = state.get("active_device") or next(reversed(state["devices"]))
    return int(addr_hex, 16)


async def _find_proxy_target(state: dict, transport, unicast: int):
    netkey = bytes.fromhex(state["netkey"])
    network_id = k3(netkey)
    scanner = transport.scanner()
    print(
        f"Scanning up to {SCAN_PROXY_S:.0f} s for the node's GATT Proxy advert "
        f"(0x1828, Network ID {network_id.hex()})..."
    )
    match, candidates = await find_proxy_node(
        scanner,
        network_id,
        timeout=SCAN_PROXY_S,
        stop_after=False,
        identity_netkey=netkey,
        node_addresses=(unicast,),
    )
    if match is not None:
        if match.identification_type == IDENTIFICATION_NETWORK_ID:
            label = "Network ID match"
        else:
            label = "verified Node Identity"
        print(f"  {label} from {match.device.address}")
        return match.device, scanner
    if candidates:
        listing = "; ".join(
            f"{c.device.address} type 0x{c.identification_type:02x} "
            f"{c.parameter.hex()}"
            for c in candidates
        )
        await scanner.stop()
        raise Phase0Failure(
            "proxy scan",
            f"{len(candidates)} 0x1828 advertisers, none matching our Network ID",
            [
                f"candidates seen: {listing}",
                "type-0x00 candidates with a different Network ID are "
                "cryptographically proven foreign (neighbor's mesh)",
                "Node Identity candidates did not authenticate for the active "
                "node address and NetKey",
            ],
        )
    # Nothing advertised 0x1828 at all: capture what the airwaves DID carry.
    entry = state["devices"].get(f"0x{unicast:04x}", {})
    seen = []
    for addr, (dev, adv) in (
        scanner.discovered_devices_and_advertisement_data.items()
    ):
        marker = " <- provisioned address" if addr == entry.get("address") else ""
        services = ", ".join(sorted(adv.service_data)) or "no service data"
        seen.append(f"{addr}: {services}{marker}")
    await scanner.stop()
    raise Phase0Failure(
        "proxy scan",
        "no 0x1828 proxy advert seen",
        [
            "node may not enable GATT Proxy by default — it must be switched "
            "on via the vendor app, or the device only supports the ADV bearer",
            "node still rebooting after provisioning — retry with --toggle-only",
            "advertisements captured during the scan window: "
            + ("; ".join(seen) if seen else "none at all"),
        ],
    )


async def configure(
    node: MeshNode, unicast: int, appkey: bytes, state: dict, pump: BearerPump
) -> None:
    print("Config: AppKey Add (netkey idx 0, appkey idx 0)...")
    t0 = time.perf_counter()
    try:
        resp = await node.request(
            unicast,
            config_appkey_add(state["netkey_index"], state["appkey_index"], appkey),
            OP_CONFIG_APPKEY_STATUS,
            dev_key=True,
            timeout=10,
        )
    except TimeoutError as exc:
        raise Phase0Failure("config AppKey Add", str(exc), _timeout_causes(pump))
    status = parse_config_appkey_status(_full_payload(resp))
    if status.status != 0x00:
        name = STATUS_NAMES.get(status.status, "Unknown")
        raise Phase0Failure(
            "config AppKey Add",
            f"AppKey Status 0x{status.status:02x} ({name})",
            [
                "0x06 Key Index Already Stored with a DIFFERENT key means the "
                "node kept an old appkey — factory-reset it and start over",
            ],
        )
    print(f"  AppKey Status: Success ({(time.perf_counter() - t0) * 1000:.0f} ms)")

    # Enumerate the node's models: the Häfele lamp answered Invalid Model to
    # a blind Generic OnOff bind, so pick the control model from what the
    # composition data actually lists. The status is SEGMENTED and we send no
    # segment acks, so the node retransmits — the assembler handles the
    # duplicates; allow a generous timeout.
    print("Config: Composition Data Get (page 0)...")
    try:
        resp = await node.request(
            unicast,
            config_composition_data_get(0),
            OP_CONFIG_COMPOSITION_DATA_STATUS,
            dev_key=True,
            timeout=15,
        )
    except TimeoutError as exc:
        raise Phase0Failure(
            "config Composition Data Get", str(exc), _timeout_causes(pump)
        )
    comp = parse_composition_data_status(_full_payload(resp))
    print(f"  composition: {comp.describe()}")
    logger.info("composition data: %s", comp.describe())
    state["composition"] = {
        "page": comp.page,
        "company_id": comp.cid,
        "product_id": comp.pid,
        "version_id": comp.vid,
        "replay_cache_size": comp.crpl,
        "features": comp.features,
        "elements": [
            {
                "address": unicast + index,
                "location": element.loc,
                "sig_models": list(element.sig_models),
                "vendor_models": [
                    {"company_id": company_id, "model_id": model_id}
                    for company_id, model_id in element.vendor_models
                ],
            }
            for index, element in enumerate(comp.elements)
        ],
    }

    model_id = _pick_control_model(comp)
    state["control_model"] = model_id

    # Mirror the active Thingclips/Olight 3.2.5 setup path. A single valid
    # OnOff bind was acknowledged but the Sphere later rolled back to 0x1827;
    # the app binds every allowlisted server before Network Transmit Set.
    allowed_sig = {0x1000, 0x1300, 0x1303, 0x1306, 0x1307}
    allowed_vendor = {(0x07D0, 0x0004)}
    bind_targets = []
    for element_index, element in enumerate(comp.elements):
        element_addr = unicast + element_index
        bind_targets.extend(
            (element_addr, sig_model, None)
            for sig_model in element.sig_models
            if sig_model in allowed_sig
        )
        bind_targets.extend(
            (element_addr, vendor_model, company_id)
            for company_id, vendor_model in element.vendor_models
            if (company_id, vendor_model) in allowed_vendor
        )

    print(f"Config: binding AppKey to {len(bind_targets)} app models...")
    for element_addr, target_model, company_id in bind_targets:
        if company_id is None:
            payload = config_model_app_bind(
                element_addr, state["appkey_index"], target_model
            )
            model_label = f"SIG 0x{target_model:04x}"
        else:
            payload = config_model_app_bind_vendor(
                element_addr,
                state["appkey_index"],
                company_id,
                target_model,
            )
            model_label = f"vendor 0x{company_id:04x}:0x{target_model:04x}"
        try:
            resp = await node.request(
                unicast,
                payload,
                OP_CONFIG_MODEL_APP_STATUS,
                dev_key=True,
                timeout=10,
            )
        except TimeoutError as exc:
            raise Phase0Failure(
                "config Model App Bind",
                f"{model_label} on 0x{element_addr:04x}: {exc}",
                _timeout_causes(pump),
            )
        status = parse_config_model_app_status(_full_payload(resp))
        if status.status != 0x00:
            name = STATUS_NAMES.get(status.status, "Unknown")
            raise Phase0Failure(
                "config Model App Bind",
                f"Model App Status 0x{status.status:02x} ({name}) binding "
                f"{model_label} on 0x{element_addr:04x}",
                ["unexpected — check the composition dump and hex log"],
            )
        print(f"  0x{element_addr:04x} {model_label}: Success")

    print("Config: Network Transmit Set (count 5, interval steps 2)...")
    try:
        resp = await node.request(
            unicast,
            config_network_transmit_set(5, 2),
            OP_CONFIG_NETWORK_TRANSMIT_STATUS,
            dev_key=True,
            timeout=10,
        )
    except TimeoutError as exc:
        raise Phase0Failure(
            "config Network Transmit Set", str(exc), _timeout_causes(pump)
        )
    if resp.params != b"\x15":
        raise Phase0Failure(
            "config Network Transmit Set",
            f"unexpected status payload {resp.params.hex()} (wanted 15)",
            ["node did not retain the requested count/interval settings"],
        )
    state["official_config_complete"] = True
    print("  Network Transmit Status: count 5, interval steps 2")


MODEL_NAMES = {
    MODEL_GENERIC_ONOFF_SERVER: "Generic OnOff Server",
    MODEL_LIGHT_LIGHTNESS_SERVER: "Light Lightness Server",
}


def _pick_control_model(comp) -> int:
    """Choose the control model from the primary element's composition data."""
    sig_models = comp.elements[0].sig_models if comp.elements else ()
    for model_id in (MODEL_GENERIC_ONOFF_SERVER, MODEL_LIGHT_LIGHTNESS_SERVER):
        if model_id in sig_models:
            return model_id
    raise Phase0Failure(
        "model selection",
        "primary element hosts neither Generic OnOff (0x1000) nor Light "
        "Lightness (0x1300)",
        [
            f"composition data: {comp.describe()}",
            "if only vendor models are listed, the lamp is controlled via a "
            "Telink/vendor model — standard-model control is a NO-GO for this "
            "device (vendor-model support would be a Phase 1 decision)",
        ],
    )


async def toggle_loop(node: MeshNode, unicast: int, pump: BearerPump, state: dict) -> None:
    model_id = state.get("control_model", MODEL_GENERIC_ONOFF_SERVER)
    model_name = MODEL_NAMES.get(model_id, f"0x{model_id:04x}")
    print(f"Toggling via {model_name}: 3 ON/OFF cycles, 2 s apart...")
    tid = 0
    for cycle in range(1, 4):
        for onoff in (1, 0):
            if model_id == MODEL_LIGHT_LIGHTNESS_SERVER:
                payload = light_lightness_set(0xFFFF if onoff else 0, tid)
                expected = OP_LIGHT_LIGHTNESS_STATUS
            else:
                payload = generic_onoff_set(bool(onoff), tid)
                expected = OP_GENERIC_ONOFF_STATUS
            t0 = time.perf_counter()
            try:
                resp = await node.request(unicast, payload, expected, timeout=5)
            except TimeoutError as exc:
                raise Phase0Failure(
                    f"toggle (cycle {cycle}, {'ON' if onoff else 'OFF'})",
                    str(exc),
                    _timeout_causes(
                        pump,
                        ["AppKey bind lost (rerun without --toggle-only)"],
                    ),
                )
            ms = (time.perf_counter() - t0) * 1000
            if model_id == MODEL_LIGHT_LIGHTNESS_SERVER:
                lstatus = parse_light_lightness_status(_full_payload(resp))
                present = lstatus.present_lightness
            else:
                gstatus = parse_generic_onoff_status(_full_payload(resp))
                present = gstatus.present_onoff
            print(
                f"  cycle {cycle} {'ON ' if onoff else 'OFF'}: "
                f"present={present} ({ms:.0f} ms)"
            )
            tid = (tid + 1) & 0xFF
            await asyncio.sleep(2.0)
    print()
    print("=== MILESTONE B: TOGGLE OK — GO ===")


async def read_control_state(
    node: MeshNode, unicast: int, pump: BearerPump, state: dict
) -> None:
    """Prove app-key model access without changing the light output."""
    model_id = state.get("control_model", MODEL_GENERIC_ONOFF_SERVER)
    if model_id == MODEL_LIGHT_LIGHTNESS_SERVER:
        payload = light_lightness_get()
        expected = OP_LIGHT_LIGHTNESS_STATUS
        label = "Light Lightness"
    else:
        payload = generic_onoff_get()
        expected = OP_GENERIC_ONOFF_STATUS
        label = "Generic OnOff"
    print(f"Read-only {label} Get using the bound AppKey...")
    try:
        resp = await node.request(unicast, payload, expected, timeout=5)
    except TimeoutError as exc:
        raise Phase0Failure(
            "read-only control check",
            str(exc),
            _timeout_causes(pump, ["AppKey bind was not retained by the node"]),
        )
    if model_id == MODEL_LIGHT_LIGHTNESS_SERVER:
        status = parse_light_lightness_status(_full_payload(resp))
        print(f"  Light Lightness Status: present={status.present_lightness}")
    else:
        status = parse_generic_onoff_status(_full_payload(resp))
        print(f"  Generic OnOff Status: present={status.present_onoff}")
    print("=== MILESTONE B: READ-ONLY APPKEY CONTROL OK — GO ===")


async def mesh_session(
    args, state: dict, store: StateStore, transport, unicast: int
) -> None:
    """Connect the proxy, configure (unless --toggle-only), toggle. Milestone B."""
    entry = state["devices"][f"0x{unicast:04x}"]
    netkey = bytes.fromhex(state["netkey"])
    appkey = bytes.fromhex(state["appkey"])
    device_key = bytes.fromhex(entry["device_key"])

    target, scanner = await _find_proxy_target(state, transport, unicast)
    print(f"Connecting Mesh Proxy to {target.address}...")

    def persist_seq(next_seq: int) -> None:
        store.advance_sequence(state, next_seq)

    try:
        session = await open_mesh_proxy_session(
            transport,
            target,
            netkey=netkey,
            appkey=appkey,
            device_key=device_key,
            iv_index=state["iv_index"],
            provisioner_addr=state["provisioner_addr"],
            unicast=unicast,
            seq=state["seq"],
            persist_seq=persist_seq,
        )
    finally:
        await scanner.stop()
    node = session.node
    pump = session.pump

    try:
        if args.reset_node:
            print("Config Node Reset (returning the Sphere to unprovisioned state)...")
            try:
                await node.request(
                    unicast,
                    config_node_reset(),
                    OP_NODE_RESET_STATUS,
                    dev_key=True,
                    timeout=6,
                    retries=1,
                )
                print("Node Reset Status received.")
            except TimeoutError:
                print("No status reply; nodes commonly reset before replying.")
            print("Recovery keys remain in the state file for verification.")
            return
        # --toggle-only still (re)configures when no successful bind was ever
        # recorded — AppKey re-Add with the same key is Success per §4.3.2.37.
        if not args.toggle_only or "control_model" not in state:
            await configure(node, unicast, appkey, state, pump)
        if args.no_toggle:
            await read_control_state(node, unicast, pump, state)
            print("Configuration complete; --no-toggle left the light unchanged.")
        else:
            await toggle_loop(node, unicast, pump, state)
    finally:
        await session.close()


# ------------------------------------------------------------------- main


async def async_main(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    store = StateStore(state_path)
    try:
        store.acquire()
        state = store.load_or_create()
    except (StateError, StateLockedError) as exc:
        store.release()
        print_diagnostic(
            "startup",
            exc,
            [
                "another process may already own this controller state",
                "repair the named field, or reset and reprovision before "
                "creating fresh keys",
            ],
        )
        return 1

    logger.info(
        "loaded state %s (seq=%d, %d device(s))",
        state_path, state["seq"], len(state["devices"]),
    )

    if args.init_only:
        print(f"Key state ready: {state_path}")
        print(f"Network ID: {k3(bytes.fromhex(state['netkey'])).hex()}")
        print("The NetKey and AppKey remain only in the mode-0600 state file.")
        try:
            store.save(state)
        finally:
            store.release()
        return 0

    if args.transport == "esphome":
        transport = EsphomeTransport(args.esphome_host, args.esphome_key)
    else:
        transport = LocalTransport()

    try:
        await transport.start()
    except BearerError as exc:
        print_diagnostic(
            "transport",
            exc,
            [
                "ESPHome host unreachable (name/IP, Wi-Fi)",
                "wrong --esphome-key (API noise PSK)",
                "proxy has no free BLE connection slot",
            ],
        )
        try:
            store.save(state)
        finally:
            store.release()
        return 1

    try:
        if args.toggle_only or args.reset_node:
            unicast = pick_device(state, args)
            print(f"using device 0x{unicast:04x} from {state_path}")
        else:
            unicast = await provision(args, state, state_path, transport)
            if args.provision_only:
                print("Stopped after provisioning as requested.")
                return 0
        await mesh_session(args, state, store, transport, unicast)
        return 0
    except Phase0Failure as failure:
        print_diagnostic(failure.phase, failure, failure.causes)
        return 1
    except (BtMeshError, TimeoutError, OSError) as exc:
        print_diagnostic(
            "unclassified",
            exc,
            [f"see the DEBUG hex dumps in {LOG_FILE} for the last frames on the air"],
        )
        return 1
    finally:
        try:
            store.save(state)
        finally:
            store.release()
        try:
            await transport.stop()
        except Exception as exc:
            logger.debug("transport stop failed (ignored): %s", exc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
