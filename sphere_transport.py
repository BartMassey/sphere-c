"""Reusable Bluetooth transport and GATT-session lifecycle."""

from __future__ import annotations

import asyncio
import logging
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
from btmesh.node import MeshNode
from btmesh.proxy_pdu import MSG_TYPE_NETWORK_PDU
from btmesh.pump import BearerPump

logger = logging.getLogger("sphere-transport")

GATT_SESSION_ATTEMPTS = 3


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


def _fresh_discoveries(scanner: Any) -> tuple[dict[str, tuple[Any, Any]], Any]:
    discoveries: dict[str, tuple[Any, Any]] = {}

    def detected(device: Any, advertisement_data: Any) -> None:
        discoveries[device.address] = (device, advertisement_data)

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


async def scan_unprovisioned(
    scanner: Any,
    timeout: float = 10.0,
    *,
    stop_on: bytes | None = None,
    stop_on_any: bool = False,
    poll_interval: float = 0.5,
    stop_after: bool = True,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    discoveries, remove_callback = _fresh_discoveries(scanner)
    await scanner.start()
    try:
        while True:
            found = unprovisioned_from_discoveries(discoveries)
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
        if stop_after:
            await scanner.stop()


async def find_proxy_node(
    scanner: Any,
    network_id: bytes,
    timeout: float = 15.0,
    poll_interval: float = 1.0,
    *,
    stop_after: bool = True,
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
            if loop.time() >= deadline:
                return None, list(candidates.values())
            await asyncio.sleep(poll_interval)
    finally:
        if remove_callback is not None:
            remove_callback()
        if stop_after:
            await scanner.stop()


class LocalTransport:
    """Use the Linux Bluetooth adapter through bleak."""

    async def start(self) -> None:
        pass

    def scanner(self):
        from bleak import BleakScanner

        return BleakScanner()

    async def connect(self, address_or_device, timeout: float = 30.0):
        return await connect_local(address_or_device, timeout)

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

    @property
    def failure(self) -> BaseException | None:
        return self.pump.failure or getattr(self.bearer, "failure", None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
    pump.on_error = lambda exc: logger.error("network TX failed: %s", exc)

    def send_network_pdu(raw: bytes) -> None:
        pump.put(raw)
        persist_seq(node.ctx.seq)

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
        return MeshProxySession(client, bearer, pump, node)
    except BaseException:
        await pump.stop()
        await bearer.stop()
        await disconnect_quietly(client)
        raise
