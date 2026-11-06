"""BlueZ's advertising-bearer Mesh node, controlled over D-Bus.

BlueZ owns network security, durable sequence/replay state and relaying.
This module owns application models and acknowledged access requests. Its
local node has a fresh reserved address, independent of the older GATT
controller's source address and sequence cursor.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import secrets
import uuid

from btmesh.access import (
    AccessError, OP_CONFIG_APPKEY_STATUS, OP_CONFIG_MODEL_APP_STATUS,
    OP_CONFIG_RELAY_STATUS, config_appkey_add, config_model_app_bind,
    config_model_app_bind_vendor, encode_opcode, parse_access,
)
from btmesh.node import ReceivedMessage
from dbus_fast import Message, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, MessageType, PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method

from .state import StateError
from .access import (
    OP_CONFIG_NETWORK_TRANSMIT_STATUS, config_network_transmit_set,
)


SERVICE = "org.bluez.mesh"
NETWORK_PATH = "/org/bluez/mesh"
ROOT_PATH = "/org/sphere_mesh"
APP_PATH = ROOT_PATH + "/application"
ELEMENT_PATH = APP_PATH + "/element0"
APPLICATION = "org.bluez.mesh.Application1"
ELEMENT = "org.bluez.mesh.Element1"
NODE = "org.bluez.mesh.Node1"
MANAGEMENT = "org.bluez.mesh.Management1"
PROPERTIES = "org.freedesktop.DBus.Properties"
CLIENT_MODELS = (0x0001, 0x1001, 0x1302, 0x1305, 0x1309, 0x130E)
VENDOR_MODELS = ((0x07D0, 0x0004),)
# Mesh encodes interval as (steps + 1) * 10 ms. The generic bearer
# programs legacy ADV_NONCONN_IND, whose interval must be at least 100 ms.
HOST_TRANSMIT_INTERVAL_STEPS = 9
HOST_TRANSMIT_PARAMETERS = bytes([HOST_TRANSMIT_INTERVAL_STEPS << 3])


class BluezMeshError(RuntimeError):
    """A meshd operation failed; arguments and credentials are omitted."""

    def __init__(self, operation: str, error_name: str) -> None:
        self.error_name = error_name
        super().__init__(f"{operation}: {error_name}")


def reserve_identity(state: dict, store) -> dict:
    """Durably reserve a new identity before BlueZ can transmit with it."""
    saved = state.get("bluez_mesh")
    if saved is not None:
        if not isinstance(saved, dict) or not 1 <= saved.get("unicast", 0) <= 0x7FFF:
            raise StateError("invalid saved BlueZ Mesh identity")
        if saved["unicast"] == state["provisioner_addr"]:
            raise StateError("BlueZ Mesh identity reuses the GATT controller source")
        if len(bytes.fromhex(saved["device_key"])) != 16 or len(bytes.fromhex(saved["uuid"])) != 16:
            raise StateError("invalid saved BlueZ Mesh credentials")
        return saved
    address = state["next_unicast"]
    for primary, entry in state["devices"].items():
        count = entry.get("element_count")
        if count is None:
            count = len(entry.get("composition", {}).get("elements", [])) or 1
        if int(primary, 16) <= address < int(primary, 16) + count:
            raise StateError("next unicast overlaps a saved node")
    if not 1 <= address <= 0x7FFF or address == state["provisioner_addr"]:
        raise StateError("no free address for BlueZ Mesh host")
    identity = {
        "uuid": uuid.uuid4().hex, "device_key": secrets.token_bytes(16).hex(),
        "unicast": address, "element_count": 1,
    }
    state["bluez_mesh"] = identity
    state["next_unicast"] = address + 1
    store.save(state)
    return identity


class MeshApplication(ServiceInterface):
    """Persist BlueZ's attachment token before acknowledging JoinComplete."""

    def __init__(self, session) -> None:
        super().__init__(APPLICATION)
        self.session = session

    @dbus_property(access=PropertyAccess.READ)
    def CompanyID(self) -> "q":
        return 0xFFFF

    @dbus_property(access=PropertyAccess.READ)
    def ProductID(self) -> "q":
        return 1

    @dbus_property(access=PropertyAccess.READ)
    def VersionID(self) -> "q":
        return 1

    @dbus_property(access=PropertyAccess.READ)
    def CRPL(self) -> "q":
        return 256

    @method()
    def JoinComplete(self, token: "t"):
        self.session.identity["token"] = token
        self.session.store.save(self.session.state)
        self.session._joined.set()

    @method()
    def JoinFailed(self, reason: "s"):
        self.session._join_failed = True
        self.session._joined.set()


class MeshElement(ServiceInterface):
    """Receive application and device-key messages from BlueZ."""

    def __init__(self, session) -> None:
        super().__init__(ELEMENT)
        self.session = session

    @dbus_property(access=PropertyAccess.READ)
    def Index(self) -> "y":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Location(self) -> "q":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Models(self) -> "a(qa{sv})":
        return [[model, {}] for model in CLIENT_MODELS]

    @dbus_property(access=PropertyAccess.READ)
    def VendorModels(self) -> "a(qqa{sv})":
        return [[company, model, {}] for company, model in VENDOR_MODELS]

    @method()
    def MessageReceived(self, source: "q", key_index: "q", destination: "v", data: "ay"):
        if key_index != self.session.state["appkey_index"]:
            return
        if destination.value != self.session.identity["unicast"]:
            return
        self.session.node.receive(source, bytes(data), dev_key=False)

    @method()
    def DevKeyMessageReceived(self, source: "q", remote: "b", net_index: "q", data: "ay"):
        if net_index == self.session.state["netkey_index"]:
            self.session.node.receive(source, bytes(data), dev_key=True)

    @method()
    def UpdateModelConfiguration(self, model_id: "q", config: "a{sv}"):
        pass


class BluezAccessNode:
    """The acknowledged subset of MeshNode used by our controllers."""

    def __init__(self, session) -> None:
        self.session = session
        self._waiters: list[tuple[int, int, bool, asyncio.Future]] = []
        self.received = deque(maxlen=128)
        self.on_message = None
        self._closed = False

    def receive(self, source: int, payload: bytes, *, dev_key: bool) -> None:
        try:
            opcode, params = parse_access(payload)
        except AccessError:
            return
        message = ReceivedMessage(source, opcode, params)
        self.received.append(message)
        for dst, expected, key_kind, future in self._waiters:
            if (dst, expected, key_kind) == (source, opcode, dev_key) and not future.done():
                future.set_result(message)
                break
        if self.on_message is not None:
            self.on_message(message)

    async def request(self, dst, payload, expected_opcode, *, dev_key=False,
                      ttl=7, timeout=5.0, retries=1) -> ReceivedMessage:
        if self._closed:
            raise BluezMeshError("request", "session closed")
        if retries < 0:
            raise ValueError("retries must be nonnegative")
        future = asyncio.get_running_loop().create_future()
        waiter = (dst, expected_opcode, dev_key, future)
        self._waiters.append(waiter)
        try:
            for _ in range(retries + 1):
                await self.session.send(dst, payload, dev_key=dev_key, ttl=ttl)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), timeout)
                except TimeoutError:
                    continue
            if future.done():
                return future.result()
            raise TimeoutError(f"no {expected_opcode:#06x} response from {dst:#06x}")
        finally:
            self._waiters.remove(waiter)
            if not future.done():
                future.cancel()

    def close(self) -> None:
        self._closed = True
        for _, _, _, future in self._waiters:
            if not future.done():
                future.set_exception(BluezMeshError("request", "session closed"))


class BluezMeshSession:
    """Attached local Mesh node; BlueZ retains its network state on close."""

    def __init__(self, state: dict, store, bus) -> None:
        self.state, self.store, self.bus = state, store, bus
        self.identity = reserve_identity(state, store)
        self.node = BluezAccessNode(self)
        self.node_path = None
        self.features: dict = {}
        self.properties: dict = {}
        self._joined = asyncio.Event()
        self._join_failed = False
        self._lost = asyncio.Event()
        self._owner = None
        self._closed = False
        self._watch_task = None

    @property
    def links(self) -> list[dict]:
        return [{"bearer": "advertising", "local_address": f"0x{self.identity['unicast']:04x}",
                 "relay": self.features.get("Relay", False)}]

    async def call(self, path, interface, member, signature="", body=None, *, destination=SERVICE):
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination=destination, path=path, interface=interface, member=member,
            signature=signature, body=[] if body is None else body,
        )), timeout=30)
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            raise BluezMeshError(member, reply.error_name if reply else "no reply")
        return reply.body

    def _guard(self, message):
        if (message.message_type == MessageType.METHOD_CALL
                and message.path in (ROOT_PATH, APP_PATH, ELEMENT_PATH)
                and message.sender != self._owner):
            return Message.new_error(message, "org.freedesktop.DBus.Error.AccessDenied",
                                     "Only bluetooth-meshd may invoke Mesh callbacks")
        if (message.message_type == MessageType.SIGNAL
                and message.sender == "org.freedesktop.DBus"
                and message.interface == "org.freedesktop.DBus"
                and message.member == "NameOwnerChanged"
                and message.body[0] == SERVICE
                and message.body[2] != self._owner):
            self._lost.set()
        return None

    async def start(self) -> None:
        owner, = await self.call("/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "GetNameOwner", "s", [SERVICE], destination="org.freedesktop.DBus")
        self._owner = owner
        self.bus.add_message_handler(self._guard)
        self.bus.export(APP_PATH, MeshApplication(self))
        self.bus.export(ELEMENT_PATH, MeshElement(self))
        await self.call("/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", [
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
            "member='NameOwnerChanged',arg0='org.bluez.mesh'"
        ], destination="org.freedesktop.DBus")
        if "token" not in self.identity:
            try:
                await self.call(NETWORK_PATH, "org.bluez.mesh.Network1", "Import",
                                "oayayayqa{sv}uq", [
                    ROOT_PATH, bytes.fromhex(self.identity["uuid"]),
                    bytes.fromhex(self.identity["device_key"]), bytes.fromhex(self.state["netkey"]),
                    self.state["netkey_index"], {}, self.state["iv_index"], self.identity["unicast"],
                ])
            except BluezMeshError as exc:
                if exc.error_name.endswith("AlreadyExists"):
                    raise BluezMeshError("Import", "identity exists but attachment token is missing; recovery required") from exc
                raise
            await asyncio.wait_for(self._joined.wait(), 30)
            if self._join_failed or "token" not in self.identity:
                raise BluezMeshError("Import", "join failed")
        attached = await self.call(NETWORK_PATH, "org.bluez.mesh.Network1", "Attach", "ot",
                                   [ROOT_PATH, self.identity["token"]])
        self.node_path = attached[0]
        await self.refresh_devices()
        await self.configure_local()
        self._watch_task = asyncio.create_task(self._watch_bus())

    async def refresh_devices(self) -> None:
        """Import keys only into BlueZ's local database; no lamp writes."""
        # Import, unlike CreateNetwork, does not seed the admin keyring with
        # our own DeviceKey. BlueZ's required remote-addressed loopback uses
        # that keyring entry when sending to its own Config Server.
        await self.call(self.node_path, MANAGEMENT, "ImportRemoteNode", "qyay", [
            self.identity["unicast"], 1, bytes.fromhex(self.identity["device_key"]),
        ])
        # Network1.Import installs the subnet in the local network stack.
        # Management1 also needs a keyring entry for administering AppKeys.
        try:
            await self.call(self.node_path, MANAGEMENT, "ImportSubnet", "qay", [
                self.state["netkey_index"], bytes.fromhex(self.state["netkey"]),
            ])
        except BluezMeshError as exc:
            if not exc.error_name.endswith("AlreadyExists"):
                raise
        try:
            await self.call(self.node_path, MANAGEMENT, "ImportAppKey", "qqay", [
                self.state["netkey_index"], self.state["appkey_index"], bytes.fromhex(self.state["appkey"]),
            ])
        except BluezMeshError as exc:
            if not exc.error_name.endswith("AlreadyExists"):
                raise
        for key, entry in self.state["devices"].items():
            if entry.get("superseded_by") or entry.get("forgotten"):
                continue
            count = entry.get("element_count") or len(entry.get("composition", {}).get("elements", []))
            if not count:
                raise StateError(f"element count missing for {key}; inspect composition first")
            await self.call(self.node_path, MANAGEMENT, "ImportRemoteNode", "qyay", [
                int(key, 16), count, bytes.fromhex(entry["device_key"]),
            ])

    async def send(self, dst: int, payload: bytes, *, dev_key=False, ttl=7) -> None:
        if self._closed or self._lost.is_set():
            raise BluezMeshError("send", "Mesh session unavailable")
        # TTL is a node setting in the public BlueZ API. Startup sets it to 7;
        # reject unsupported per-request overrides instead of ignoring them.
        if ttl != 7:
            raise ValueError("BlueZ backend currently supports default TTL 7")
        if dev_key:
            # BlueZ explicitly requires remote addressing for loopbacks to
            # local configuration servers (node.c:dev_key_send_call).
            await self.call(self.node_path, NODE, "DevKeySend", "oqbqa{sv}ay", [
                ELEMENT_PATH, dst, True, self.state["netkey_index"], {}, payload,
            ])
        else:
            await self.call(self.node_path, NODE, "Send", "oqqa{sv}ay", [
                ELEMENT_PATH, dst, self.state["appkey_index"], {}, payload,
            ])

    async def configure_local(self) -> None:
        """Bind clients and apply conservative host transmission settings.

        Single transmissions avoid BlueZ's repeated-packet queue path.
        A 100 ms interval also permits legacy nonconnectable advertising
        on the generic bearer; the Mesh minimum of 10 ms does not.
        Disabling this host's secure beacons removes periodic transmit
        load; other network nodes keep their beacon settings. These are
        bounded mitigations for the observed MGMT backend failures, not
        a repair of BlueZ's packet lifetime or controller state bugs.
        """
        address = self.identity["unicast"]
        response = await self.node.request(address, config_appkey_add(
            self.state["netkey_index"], self.state["appkey_index"], bytes.fromhex(self.state["appkey"]),
        ), OP_CONFIG_APPKEY_STATUS, dev_key=True)
        if not response.params or response.params[0] != 0:
            raise BluezMeshError("local AppKey Add", "non-success status")
        bindings = [config_model_app_bind(address, self.state["appkey_index"], model)
                    for model in CLIENT_MODELS if model != 0x0001]
        bindings += [config_model_app_bind_vendor(address, self.state["appkey_index"], company, model)
                     for company, model in VENDOR_MODELS]
        for payload in bindings:
            response = await self.node.request(address, payload, OP_CONFIG_MODEL_APP_STATUS, dev_key=True)
            if not response.params or response.params[0] != 0:
                raise BluezMeshError("local Model App Bind", "non-success status")
        ttl = await self.node.request(address, b"\x80\x0d\x07", 0x800E, dev_key=True)
        if ttl.params != b"\x07":
            raise BluezMeshError("local Default TTL", "unexpected status")
        transmit = await self.node.request(
            address, config_network_transmit_set(0, HOST_TRANSMIT_INTERVAL_STEPS),
            OP_CONFIG_NETWORK_TRANSMIT_STATUS, dev_key=True,
        )
        if transmit.params != HOST_TRANSMIT_PARAMETERS:
            raise BluezMeshError("local Network Transmit Set", "unexpected status")
        relay = await self.node.request(
            address, b"\x80\x27\x01" + HOST_TRANSMIT_PARAMETERS,
            OP_CONFIG_RELAY_STATUS, dev_key=True,
        )
        if relay.params != b"\x01" + HOST_TRANSMIT_PARAMETERS:
            raise BluezMeshError("local Relay Set", "unexpected status")
        beacon = await self.node.request(address, b"\x80\x0a\x00", 0x800B, dev_key=True)
        if beacon.params != b"\x00":
            raise BluezMeshError("local Beacon Set", "unexpected status")
        await self.refresh_properties()
        if self.features.get("Relay") is not True:
            raise BluezMeshError("local Relay", "relay not enabled")
        if self.properties.get("Beacon") is not False:
            raise BluezMeshError("local Beacon", "beacons still enabled")

    async def refresh_properties(self) -> dict:
        values, = await self.call(self.node_path, PROPERTIES, "GetAll", "s", [NODE])
        self.properties = {key: value.value for key, value in values.items()}
        self.features = {key: value.value for key, value in self.properties.get("Features", {}).items()}
        return self.properties

    @asynccontextmanager
    async def pause_advertising(self):
        """Quiesce this node's relay and beacons during GATT commissioning.

        Keep bluetoothd, its audio connections and our D-Bus attachment
        intact. Restore the original states even if commissioning fails.
        """
        await self.refresh_properties()
        relay = bool(self.features.get("Relay"))
        beacon = bool(self.properties.get("Beacon"))
        address = self.identity["unicast"]
        try:
            response = await self.node.request(
                address, b"\x80\x27\x00" + HOST_TRANSMIT_PARAMETERS,
                OP_CONFIG_RELAY_STATUS, dev_key=True,
            )
            if response.params[:1] != b"\x00":
                raise BluezMeshError("pause Relay", "unexpected status")
            response = await self.node.request(
                address, b"\x80\x0a\x00", 0x800B, dev_key=True,
            )
            if response.params != b"\x00":
                raise BluezMeshError("pause Beacon", "unexpected status")
            await asyncio.sleep(0.3)
            yield
        finally:
            try:
                try:
                    response = await self.node.request(
                        address, b"\x80\x0a" + bytes([beacon]), 0x800B, dev_key=True,
                    )
                    if response.params != bytes([beacon]):
                        raise BluezMeshError("restore Beacon", "unexpected status")
                finally:
                    response = await self.node.request(
                        address, b"\x80\x27" + bytes([relay]) + HOST_TRANSMIT_PARAMETERS,
                        OP_CONFIG_RELAY_STATUS, dev_key=True,
                    )
                    if response.params != bytes([relay]) + HOST_TRANSMIT_PARAMETERS:
                        raise BluezMeshError("restore Relay", "unexpected status")
                await self.refresh_properties()
            except BaseException:
                self._lost.set()
                raise

    async def _watch_bus(self) -> None:
        try:
            await self.bus.wait_for_disconnect()
        finally:
            self._lost.set()

    async def wait_lost(self) -> BaseException:
        await self._lost.wait()
        return BluezMeshError("session", "bluetooth-meshd or D-Bus disconnected")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.node.close()
        self._lost.set()
        if self._watch_task is not None:
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
        self.bus.disconnect()


async def open_bluez_mesh_session(state: dict, store, *, bus_address=None) -> BluezMeshSession:
    """Attach to meshd with a fresh, durably reserved local node identity."""
    bus = await MessageBus(bus_address=bus_address, bus_type=BusType.SYSTEM).connect()
    session = None
    try:
        session = BluezMeshSession(state, store, bus)
        await session.start()
        return session
    except BaseException:
        if session is not None:
            await session.close()
        else:
            bus.disconnect()
        raise
