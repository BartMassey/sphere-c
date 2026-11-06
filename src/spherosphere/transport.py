"""Reusable Bluetooth transport and GATT-session lifecycle."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from btmesh.bearer import (
    IDENTIFICATION_NETWORK_ID,
    IDENTIFICATION_NODE_IDENTITY,
    PROV_DATA_IN,
    PROV_DATA_OUT,
    PROV_SERVICE,
    PROXY_DATA_IN,
    PROXY_DATA_OUT,
    PROXY_SERVICE,
    BearerError,
    GattBearer as UpstreamGattBearer,
    connect_local,
    proxy_candidates_from_discoveries,
    unprovisioned_from_discoveries,
)
from btmesh.crypto import aes_ecb, k1, s1
from btmesh.node import MeshNode
from btmesh.proxy_config import FILTER_ACCEPT_LIST, add_addresses, set_filter_type
from btmesh.proxy_pdu import MSG_TYPE_NETWORK_PDU, MSG_TYPE_PROXY_CONFIG
from btmesh.pump import BearerPump

logger = logging.getLogger("spherosphere-transport")

GATT_SESSION_ATTEMPTS = 3


def node_identity_matches(
    netkey: bytes, unicast: int, parameter: bytes
) -> bool:
    if len(parameter) != 16 or not 0x0001 <= unicast <= 0x7FFF:
        return False
    advertised_hash = parameter[:8]
    random = parameter[8:]
    identity_key = k1(netkey, s1(b"nkik"), b"id128\x01")
    block = bytes(6) + random + unicast.to_bytes(2, "big")
    return aes_ecb(identity_key, block)[8:] == advertised_hash


def _characteristic_for_service(client: Any, service_uuid: str, char_uuid: str):
    services = getattr(client, "services", None)
    if services is None:
        return char_uuid
    matches = [
        characteristic
        for service in services
        if str(service.uuid).lower() == service_uuid
        for characteristic in service.characteristics
        if str(characteristic.uuid).lower() == char_uuid
    ]
    if not matches:
        return char_uuid
    selected = min(matches, key=lambda characteristic: characteristic.handle)
    if len(matches) > 1:
        logger.warning(
            "service %s exposes %d copies of %s; selecting handle 0x%04x",
            service_uuid,
            len(matches),
            char_uuid,
            selected.handle,
        )
    return selected


class GattBearer(UpstreamGattBearer):
    """Upstream bearer with service-specific characteristic selection."""

    def __init__(self, client: Any, *, provisioning: bool = False) -> None:
        super().__init__(client, provisioning=provisioning)
        if provisioning:
            service = PROV_SERVICE
            data_in, data_out = PROV_DATA_IN, PROV_DATA_OUT
        else:
            service = PROXY_SERVICE
            data_in, data_out = PROXY_DATA_IN, PROXY_DATA_OUT
        self._data_in = _characteristic_for_service(client, service, data_in)
        self._data_out = _characteristic_for_service(client, service, data_out)


def _fresh_discoveries(scanner: Any, on_detection: Callable | None = None) -> tuple[dict[str, tuple[Any, Any]], Any]:
    discoveries: dict[str, tuple[Any, Any]] = {}

    def detected(device: Any, advertisement_data: Any) -> None:
        discoveries[device.address] = (device, advertisement_data)
        if on_detection is not None:
            on_detection(device, advertisement_data)

    register = getattr(scanner, "register_detection_callback", None)
    if not callable(register):
        register = getattr(
            getattr(scanner, "_backend", None),
            "register_detection_callback",
            None,
        )
    if callable(register):
        return discoveries, register(detected)
    return scanner.discovered_devices_and_advertisement_data, None


def watch_unprovisioned(scanner: Any, on_discovered: Callable):
    """Observe advertisements until the returned unsubscribe callback is called.

    Return None when the scanner does not support detection callbacks.
    """
    def detected(device, advertisement):
        for beacon in unprovisioned_from_discoveries({device.address: (device, advertisement)}):
            on_discovered(beacon)

    register = getattr(scanner, "register_detection_callback", None)
    if not callable(register):
        register = getattr(getattr(scanner, "_backend", None), "register_detection_callback", None)
    return register(detected) if callable(register) else None


async def scan_unprovisioned(
    scanner: Any,
    timeout: float = 10.0,
    *,
    stop_on: bytes | None = None,
    stop_on_any: bool = False,
    poll_interval: float = 0.5,
    stop_after: bool = True,
    on_discovered: Callable | None = None,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    def detected(device, advertisement):
        if on_discovered is not None:
            for beacon in unprovisioned_from_discoveries({device.address: (device, advertisement)}):
                on_discovered(beacon)

    discoveries, remove_callback = _fresh_discoveries(scanner, detected)
    started = False
    try:
        await scanner.start()
        started = True
        while True:
            found = unprovisioned_from_discoveries(discoveries)
            if remove_callback is None and on_discovered is not None:
                for beacon in found:
                    on_discovered(beacon)
            if stop_on is not None and any(device.uuid == stop_on for device in found):
                return found
            if stop_on_any and found:
                return found
            if loop.time() >= deadline:
                return found
            await asyncio.sleep(poll_interval)
    finally:
        if remove_callback is not None:
            remove_callback()
        if stop_after and started:
            await scanner.stop()


async def find_proxy_node(
    scanner: Any,
    network_id: bytes,
    timeout: float = 15.0,
    poll_interval: float = 1.0,
    *,
    stop_after: bool = True,
    identity_netkey: bytes | None = None,
    node_addresses: tuple[int, ...] = (),
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    candidates = {}
    discoveries, remove_callback = _fresh_discoveries(scanner)
    await scanner.start()
    try:
        while True:
            for candidate in proxy_candidates_from_discoveries(discoveries):
                if candidate.device.address not in candidates:
                    if candidate.identification_type == IDENTIFICATION_NODE_IDENTITY:
                        logger.info(
                            "0x1828 Node Identity advert from %s (hash+random %s)",
                            candidate.device.address,
                            candidate.parameter.hex(),
                        )
                    else:
                        logger.info(
                            "0x1828 Network ID advert from %s: %s",
                            candidate.device.address,
                            candidate.parameter.hex(),
                        )
                candidates[candidate.device.address] = candidate
                if (
                    candidate.identification_type == IDENTIFICATION_NETWORK_ID
                    and candidate.parameter == network_id
                ):
                    return candidate, list(candidates.values())
                if (
                    candidate.identification_type == IDENTIFICATION_NODE_IDENTITY
                    and identity_netkey is not None
                    and any(
                        node_identity_matches(
                            identity_netkey, address, candidate.parameter
                        )
                        for address in node_addresses
                    )
                ):
                    logger.info(
                        "verified Node Identity from %s",
                        candidate.device.address,
                    )
                    return candidate, list(candidates.values())
            if loop.time() >= deadline:
                return None, list(candidates.values())
            await asyncio.sleep(poll_interval)
    finally:
        if remove_callback is not None:
            remove_callback()
        if stop_after:
            await scanner.stop()


def adapter_selector(value: str) -> str:
    """Validate an ordinary BlueZ adapter name or stable hardware address."""
    if re.fullmatch(r"hci[0-9]+", value):
        return value
    if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", value):
        return value.upper()
    raise ValueError("adapter must be a Bluetooth MAC address or hciN")


async def resolve_adapter(selector: str) -> str:
    """Resolve only an adapter exposed by ordinary bluetoothd; never fall back."""
    from dbus_fast import Message
    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import BusType, MessageType

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination="org.bluez", path="/",
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        ))
        if reply.message_type != MessageType.METHOD_RETURN:
            raise RuntimeError("Could not list ordinary BlueZ adapters")
        matches = []
        for path, interfaces in reply.body[0].items():
            props = interfaces.get("org.bluez.Adapter1")
            if props is None:
                continue
            name = path.rsplit("/", 1)[-1]
            if name == selector or props["Address"].value.upper() == selector:
                matches.append(name)
        if len(matches) != 1:
            raise RuntimeError(
                f"Ordinary Bluetooth adapter {selector} is unavailable; "
                "a Mesh-owned controller cannot be used for GATT"
            )
        return matches[0]
    finally:
        bus.disconnect()


class LocalTransport:
    """Use the Linux Bluetooth adapter through bleak."""

    def __init__(self, adapter: str | None = None) -> None:
        self._selector = adapter_selector(adapter) if adapter else None
        self._adapter: str | None = None

    async def start(self) -> None:
        if self._selector:
            self._adapter = await resolve_adapter(self._selector)

    def _bluez_options(self) -> dict:
        if self._selector and self._adapter is None:
            raise RuntimeError("Selected Bluetooth transport has not been started")
        return {"adapter": self._adapter} if self._adapter else {}

    def scanner(self):
        from bleak import BleakScanner

        options = self._bluez_options()
        return BleakScanner(bluez=options) if options else BleakScanner()

    async def connect(self, address_or_device, timeout: float = 30.0):
        options = self._bluez_options()
        if options:
            from bleak import BleakClient
            from bleak_retry_connector import establish_connection

            try:
                if isinstance(address_or_device, str):
                    client = BleakClient(address_or_device, timeout=timeout, bluez=options)
                    await client.connect()
                else:
                    # BLEDevice embeds its adapter's D-Bus path. Reject a
                    # cached device from another radio rather than switching.
                    path = address_or_device.details.get("path", "")
                    if not path.startswith(f"/org/bluez/{self._adapter}/dev_"):
                        raise BearerError("Scanned device belongs to another adapter")
                    client = await establish_connection(
                        BleakClient, address_or_device, address_or_device.address,
                        bluez=options,
                    )
            except Exception as exc:
                raise BearerError(f"Selected-adapter BLE connection failed: {exc}") from exc
        else:
            client = await connect_local(address_or_device, timeout)
        from bleak.backends.bluezdbus.client import BleakClientBlueZDBus

        backend = getattr(client, "_backend", None)
        if isinstance(backend, BleakClientBlueZDBus):
            try:
                await backend._acquire_mtu()
            except Exception as exc:
                backend._mtu_size = 23
                logger.warning(
                    "could not acquire BlueZ ATT MTU; using 23 bytes: %s", exc
                )
            logger.info("BlueZ ATT MTU: %d bytes", client.mtu_size)
        return client

    async def stop(self) -> None:
        pass


async def disconnect_quietly(client) -> None:
    """Disconnect a client without hiding the primary operation's error."""
    try:
        await client.disconnect()
    except Exception as exc:
        logger.debug("disconnect failed (ignored): %s", exc)


async def open_gatt_session(
    transport,
    device,
    *,
    provisioning: bool,
    on_message,
    attempts: int = GATT_SESSION_ATTEMPTS,
) -> tuple[object, GattBearer]:
    """Connect and subscribe, retrying the complete session setup."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            logger.info("retrying GATT session (%d/%d)...", attempt, attempts)
            await asyncio.sleep(2.0)
        try:
            client = await transport.connect(device)
        except BearerError as exc:
            last = exc
            logger.warning("connect attempt %d/%d failed: %s", attempt, attempts, exc)
            continue
        bearer = GattBearer(client, provisioning=provisioning)
        try:
            await bearer.start(on_message)
        except BearerError as exc:
            last = exc
            logger.warning(
                "subscribe attempt %d/%d failed: %s", attempt, attempts, exc
            )
            await disconnect_quietly(client)
            continue
        return client, bearer
    raise BearerError(
        f"could not establish a GATT session after {attempts} attempts: {last}"
    )


class MeshProxySession:
    """One connected Mesh Proxy bearer, pump, and protocol node."""

    def __init__(self, client, bearer, pump: BearerPump, node: MeshNode):
        self.client = client
        self.bearer = bearer
        self.pump = pump
        self.node = node
        self._closed = False
        self._lost = asyncio.Event()
        self._recorded_failure: BaseException | None = None
        self._loop = asyncio.get_running_loop()
        setter = getattr(client, "set_disconnected_callback", None)
        if callable(setter):
            setter(self._on_disconnected)

    def _on_disconnected(self, _client) -> None:
        failure = BearerError("GATT connection closed")
        try:
            self._loop.call_soon_threadsafe(self._mark_lost, failure)
        except RuntimeError:
            pass

    def _mark_lost(self, failure: BaseException) -> None:
        if self._recorded_failure is None:
            self._recorded_failure = failure
        self._lost.set()

    @property
    def failure(self) -> BaseException | None:
        return (
            self._recorded_failure
            or self.pump.failure
            or getattr(self.bearer, "failure", None)
        )

    async def wait_lost(self, poll_interval: float = 0.25) -> BaseException:
        while True:
            failure = self.failure
            if failure is not None:
                self._mark_lost(failure)
                return failure
            if getattr(self.client, "is_connected", True) is False:
                failure = BearerError("GATT connection is no longer connected")
                self._mark_lost(failure)
                return failure
            try:
                await asyncio.wait_for(self._lost.wait(), timeout=poll_interval)
            except TimeoutError:
                continue

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.node.close()
        await self.pump.stop()
        await self.bearer.stop()
        await disconnect_quietly(self.client)


async def open_mesh_proxy_session(
    transport,
    device,
    *,
    netkey: bytes,
    appkey: bytes,
    device_key: bytes,
    iv_index: int,
    provisioner_addr: int,
    unicast: int,
    seq: int,
    persist_seq: Callable[[int], None],
) -> MeshProxySession:
    """Open a Mesh Proxy session using already provisioned key material."""
    session_handler: list[Callable[[int, bytes], None]] = []

    def dispatch(msg_type: int, payload: bytes) -> None:
        if session_handler:
            session_handler[0](msg_type, payload)
        else:
            logger.debug("PDU before session ready: 0x%02x %s", msg_type, payload.hex())

    client, bearer = await open_gatt_session(
        transport,
        device,
        provisioning=False,
        on_message=dispatch,
    )
    pump = BearerPump(bearer, MSG_TYPE_NETWORK_PDU)
    session_holder: list[MeshProxySession] = []

    def pump_failed(exc: BaseException) -> None:
        logger.error("network TX failed: %s", exc)
        if session_holder:
            session_holder[0]._mark_lost(exc)

    pump.on_error = pump_failed

    def send_network_pdu(raw: bytes) -> None:
        persist_seq(node.ctx.seq)
        pump.put(raw)

    try:
        node = MeshNode(
            netkey=netkey,
            appkey=appkey,
            iv_index=iv_index,
            src_addr=provisioner_addr,
            send_network_pdu=send_network_pdu,
            seq=seq,
        )
        node.add_device(unicast, device_key)

        def on_message(msg_type: int, payload: bytes) -> None:
            if msg_type == MSG_TYPE_NETWORK_PDU:
                node.handle_network_pdu(payload)
            else:
                logger.debug(
                    "ignoring proxy message type 0x%02x: %s",
                    msg_type,
                    payload.hex(),
                )

        session_handler.append(on_message)
        pump.start()
        session = MeshProxySession(client, bearer, pump, node)
        session_holder.append(session)
        for message in (
            set_filter_type(FILTER_ACCEPT_LIST),
            add_addresses([provisioner_addr]),
        ):
            raw = node.build_proxy_config_pdu(message)
            persist_seq(node.ctx.seq)
            pump.put(raw, msg_type=MSG_TYPE_PROXY_CONFIG)
        return session
    except BaseException:
        await pump.stop()
        await bearer.stop()
        await disconnect_quietly(client)
        raise
