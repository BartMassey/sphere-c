import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import generic_onoff_get
from btmesh.bearer import PROV_SERVICE, PROXY_SERVICE, BearerError
from spherosphere import transport as sphere_transport


class FakeClient:
    def __init__(self):
        self.disconnect = AsyncMock()
        self.is_connected = True
        self.disconnected_callback = None

    def set_disconnected_callback(self, callback):
        self.disconnected_callback = callback


class FakeTransport:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.connect_calls = 0

    async def connect(self, device):
        self.connect_calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_selected_adapter_routes_scanning_and_connections(monkeypatch):
    import bleak
    import bleak_retry_connector

    resolve = AsyncMock(return_value="hci7")
    monkeypatch.setattr(sphere_transport, "resolve_adapter", resolve)
    scanner = Mock()
    monkeypatch.setattr(bleak, "BleakScanner", scanner)
    client = SimpleNamespace(connect=AsyncMock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(bleak, "BleakClient", factory)
    retry = AsyncMock(return_value=client)
    monkeypatch.setattr(bleak_retry_connector, "establish_connection", retry)
    transport = sphere_transport.LocalTransport(adapter="aa:bb:cc:dd:ee:ff")
    with pytest.raises(RuntimeError, match="not been started"):
        transport.scanner()
    await transport.start()
    resolve.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")
    transport.scanner()
    scanner.assert_called_once_with(bluez={"adapter": "hci7"})
    assert await transport.connect("11:22:33:44:55:66", 12) is client
    factory.assert_called_once_with("11:22:33:44:55:66", timeout=12,
                                    bluez={"adapter": "hci7"})
    device = SimpleNamespace(address="11:22:33:44:55:66",
                             details={"path": "/org/bluez/hci7/dev_11_22_33_44_55_66"})
    assert await transport.connect(device) is client
    retry.assert_awaited_once_with(factory, device, device.address,
                                  bluez={"adapter": "hci7"})
    device.details["path"] = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    with pytest.raises(BearerError, match="another adapter"):
        await transport.connect(device)
    assert retry.await_count == 1


async def test_adapter_resolution_uses_address_and_rejects_unavailable_radio(monkeypatch):
    from dbus_fast import Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import MessageType

    bus = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(
        message_type=MessageType.METHOD_RETURN,
        body=[{"/org/bluez/hci7": {"org.bluez.Adapter1": {
            "Address": Variant("s", "AA:BB:CC:DD:EE:FF"),
        }}}],
    )), disconnect=Mock())
    monkeypatch.setattr(MessageBus, "connect", AsyncMock(return_value=bus))
    assert await sphere_transport.resolve_adapter("AA:BB:CC:DD:EE:FF") == "hci7"
    assert await sphere_transport.resolve_adapter("hci7") == "hci7"
    with pytest.raises(RuntimeError, match="unavailable"):
        await sphere_transport.resolve_adapter("hci0")
    assert bus.disconnect.call_count == 3


async def test_local_transport_acquires_bluez_mtu_before_reading_it(monkeypatch):
    from bleak.backends.bluezdbus import client as bluez_client

    class FakeBlueZBackend:
        def __init__(self):
            self._mtu_size = None
            self.acquire_calls = 0

        async def _acquire_mtu(self):
            self.acquire_calls += 1
            self._mtu_size = 69

    class Client:
        def __init__(self):
            self._backend = FakeBlueZBackend()

        @property
        def mtu_size(self):
            assert self._backend._mtu_size is not None
            return self._backend._mtu_size

    client = Client()
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(bluez_client, "BleakClientBlueZDBus", FakeBlueZBackend)
    monkeypatch.setattr(sphere_transport, "connect_local", connect)

    assert await sphere_transport.LocalTransport().connect("device", 12.0) is client
    connect.assert_awaited_once_with("device", 12.0)
    assert client._backend.acquire_calls == 1
    assert client.mtu_size == 69
    assert sphere_transport.GattBearer(client).max_frame == 66


async def test_local_transport_uses_minimum_mtu_if_acquisition_fails(monkeypatch):
    from bleak.backends.bluezdbus import client as bluez_client

    class FakeBlueZBackend:
        def __init__(self):
            self._mtu_size = None

        async def _acquire_mtu(self):
            raise OSError("AcquireWrite unavailable")

    class Client:
        def __init__(self):
            self._backend = FakeBlueZBackend()

        @property
        def mtu_size(self):
            assert self._backend._mtu_size is not None
            return self._backend._mtu_size

    client = Client()
    monkeypatch.setattr(bluez_client, "BleakClientBlueZDBus", FakeBlueZBackend)
    monkeypatch.setattr(
        sphere_transport, "connect_local", AsyncMock(return_value=client)
    )

    assert await sphere_transport.LocalTransport().connect("device") is client
    assert client.mtu_size == 23
    assert sphere_transport.GattBearer(client).max_frame == 20


def bearer_factory(start_outcomes):
    outcomes = iter(start_outcomes)
    bearers = []

    class FakeBearer:
        def __init__(self, client, *, provisioning):
            self.client = client
            self.provisioning = provisioning
            self.start = AsyncMock(side_effect=next(outcomes))
            bearers.append(self)

    return FakeBearer, bearers


async def test_open_gatt_session_retries_connect(monkeypatch):
    client = FakeClient()
    transport = FakeTransport([BearerError("dropped"), client])
    fake_bearer, bearers = bearer_factory([None])
    sleep = AsyncMock()
    monkeypatch.setattr(sphere_transport, "GattBearer", fake_bearer)
    monkeypatch.setattr(sphere_transport.asyncio, "sleep", sleep)
    on_message = object()

    result_client, result_bearer = await sphere_transport.open_gatt_session(
        transport,
        "device",
        provisioning=False,
        on_message=on_message,
        attempts=2,
    )

    assert result_client is client
    assert result_bearer is bearers[0]
    assert result_bearer.provisioning is False
    result_bearer.start.assert_awaited_once_with(on_message)
    sleep.assert_awaited_once_with(2.0)


async def test_open_gatt_session_disconnects_after_subscribe_failure(monkeypatch):
    first = FakeClient()
    second = FakeClient()
    transport = FakeTransport([first, second])
    fake_bearer, bearers = bearer_factory([BearerError("subscribe"), None])
    monkeypatch.setattr(sphere_transport, "GattBearer", fake_bearer)
    monkeypatch.setattr(sphere_transport.asyncio, "sleep", AsyncMock())

    result_client, result_bearer = await sphere_transport.open_gatt_session(
        transport,
        "device",
        provisioning=True,
        on_message=lambda *_: None,
        attempts=2,
    )

    assert result_client is second
    assert result_bearer is bearers[1]
    first.disconnect.assert_awaited_once()
    second.disconnect.assert_not_awaited()


async def test_open_gatt_session_reports_exhausted_attempts(monkeypatch):
    transport = FakeTransport([BearerError("one"), BearerError("two")])
    monkeypatch.setattr(sphere_transport.asyncio, "sleep", AsyncMock())

    with pytest.raises(BearerError, match="after 2 attempts: two"):
        await sphere_transport.open_gatt_session(
            transport,
            "device",
            provisioning=False,
            on_message=lambda *_: None,
            attempts=2,
        )


async def test_disconnect_quietly_swallows_disconnect_error():
    client = FakeClient()
    client.disconnect.side_effect = OSError("gone")

    await sphere_transport.disconnect_quietly(client)

    client.disconnect.assert_awaited_once()


async def test_open_gatt_session_rejects_zero_attempts():
    with pytest.raises(ValueError, match="at least 1"):
        await sphere_transport.open_gatt_session(
            FakeTransport([]),
            "device",
            provisioning=False,
            on_message=lambda *_: None,
            attempts=0,
        )


class FakeProxyBearer:
    def __init__(self):
        self.sent = []
        self.stop = AsyncMock()
        self.failure = None

    async def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


@pytest.mark.parametrize("message,foreign_key,raw,expected", [
    (bytes([3, 0, 0, 2]), False, False, "type=0x00 list_size=2"),
    (bytes([3, 1, 1, 0]), False, False, "type=0x01 list_size=256"),
    (bytes([3, 0]), False, False, None),
    (bytes([4, 0, 0, 2]), False, False, None),
    (bytes([3, 0, 0, 2]), True, False, None),
    (bytes([3, 0, 0, 2]), False, True, None),
])
async def test_proxy_status_logging_requires_authenticated_network_envelope(
    monkeypatch, caplog, message, foreign_key, raw, expected,
):
    from btmesh.node import MeshNode

    client = FakeClient()
    bearer = FakeProxyBearer()
    open_session = AsyncMock(return_value=(client, bearer))
    monkeypatch.setattr(sphere_transport, "open_gatt_session", open_session)
    session = await sphere_transport.open_mesh_proxy_session(
        object(), "device", netkey=bytes(16), appkey=bytes([1]) * 16,
        device_key=bytes([2]) * 16, iv_index=0, provisioner_addr=1,
        unicast=0x0100, seq=10, persist_seq=lambda _: None,
    )
    peer = MeshNode(
        netkey=bytes([3 if foreign_key else 0]) * 16,
        appkey=bytes([1]) * 16, iv_index=0, src_addr=0x0100,
        send_network_pdu=lambda _: None,
    )
    try:
        callback = open_session.await_args.kwargs["on_message"]
        payload = message if raw else peer.build_proxy_config_pdu(message)
        if not raw:
            assert len(payload) > 4
        session.node.handle_network_pdu = Mock()
        caplog.set_level("INFO", logger="spherosphere-transport")

        callback(sphere_transport.MSG_TYPE_PROXY_CONFIG, payload)

        status_logs = [record.getMessage() for record in caplog.records
                       if "Proxy Filter Status:" in record.getMessage()]
        if expected is None:
            assert status_logs == []
        else:
            assert status_logs == [f"Proxy Filter Status: {expected}"]
        session.node.handle_network_pdu.assert_not_called()
        assert session.failure is None
    finally:
        peer.close()
        await session.close()


async def test_mesh_proxy_session_persists_seq_and_closes_once(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    open_session = AsyncMock(return_value=(client, bearer))
    monkeypatch.setattr(sphere_transport, "open_gatt_session", open_session)
    persisted = []

    session = await sphere_transport.open_mesh_proxy_session(
        object(),
        "device",
        netkey=bytes.fromhex("00112233445566778899aabbccddeeff"),
        appkey=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
        device_key=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        iv_index=0,
        provisioner_addr=1,
        unicast=0x0112,
        seq=10,
        persist_seq=persisted.append,
    )

    session.node.send_access(0x0112, generic_onoff_get())
    assert persisted == [11, 12, 13]
    await session.pump.flush()
    assert [msg_type for msg_type, _ in bearer.sent] == [
        sphere_transport.MSG_TYPE_PROXY_CONFIG,
        sphere_transport.MSG_TYPE_PROXY_CONFIG,
        sphere_transport.MSG_TYPE_NETWORK_PDU,
    ]
    assert session.failure is None

    await session.close()
    await session.close()

    bearer.stop.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    open_session.assert_awaited_once()
    assert open_session.await_args.kwargs["provisioning"] is False


async def test_mesh_proxy_session_persists_before_queueing(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    monkeypatch.setattr(
        sphere_transport,
        "open_gatt_session",
        AsyncMock(return_value=(client, bearer)),
    )

    persisted = []

    def fail_persistence(next_seq):
        persisted.append(next_seq)
        if len(persisted) == 3:
            raise OSError("disk failed")

    session = await sphere_transport.open_mesh_proxy_session(
        object(),
        "device",
        netkey=bytes(16),
        appkey=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
        device_key=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        iv_index=0,
        provisioner_addr=1,
        unicast=0x0118,
        seq=10,
        persist_seq=fail_persistence,
    )

    with pytest.raises(OSError, match="disk failed"):
        session.node.send_access(0x0118, generic_onoff_get())
    await session.pump.flush()
    assert len(bearer.sent) == 2
    assert all(
        msg_type == sphere_transport.MSG_TYPE_PROXY_CONFIG
        for msg_type, _ in bearer.sent
    )
    await session.close()


async def test_mesh_proxy_session_closes_protocol_node(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    monkeypatch.setattr(
        sphere_transport,
        "open_gatt_session",
        AsyncMock(return_value=(client, bearer)),
    )
    session = await sphere_transport.open_mesh_proxy_session(
        object(),
        "device",
        netkey=bytes(16),
        appkey=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
        device_key=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        iv_index=0,
        provisioner_addr=1,
        unicast=0x0118,
        seq=10,
        persist_seq=lambda _: None,
    )
    session.node.close = Mock()

    await session.close()
    await session.close()

    session.node.close.assert_called_once_with()


async def test_mesh_proxy_session_reports_client_disconnect(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    monkeypatch.setattr(
        sphere_transport,
        "open_gatt_session",
        AsyncMock(return_value=(client, bearer)),
    )
    session = await sphere_transport.open_mesh_proxy_session(
        object(),
        "device",
        netkey=bytes(16),
        appkey=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
        device_key=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        iv_index=0,
        provisioner_addr=1,
        unicast=0x0118,
        seq=10,
        persist_seq=lambda _: None,
    )

    client.disconnected_callback(client)
    failure = await asyncio.wait_for(session.wait_lost(), timeout=1)

    assert "connection closed" in str(failure)
    await session.close()


async def test_mesh_proxy_session_polls_late_bearer_failure(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    monkeypatch.setattr(
        sphere_transport,
        "open_gatt_session",
        AsyncMock(return_value=(client, bearer)),
    )
    session = await sphere_transport.open_mesh_proxy_session(
        object(),
        "device",
        netkey=bytes(16),
        appkey=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
        device_key=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
        iv_index=0,
        provisioner_addr=1,
        unicast=0x0118,
        seq=10,
        persist_seq=lambda _: None,
    )
    bearer.failure = BearerError("notification failed")

    failure = await asyncio.wait_for(
        session.wait_lost(poll_interval=0.001), timeout=1
    )

    assert str(failure) == "notification failed"
    await session.close()


async def test_mesh_proxy_session_cleans_up_invalid_key(monkeypatch):
    client = FakeClient()
    bearer = FakeProxyBearer()
    monkeypatch.setattr(
        sphere_transport,
        "open_gatt_session",
        AsyncMock(return_value=(client, bearer)),
    )

    with pytest.raises(Exception, match="device key must be 16 bytes"):
        await sphere_transport.open_mesh_proxy_session(
            object(),
            "device",
            netkey=bytes(16),
            appkey=bytes(16),
            device_key=b"short",
            iv_index=0,
            provisioner_addr=1,
            unicast=0x0112,
            seq=0,
            persist_seq=lambda _: None,
        )

    bearer.stop.assert_awaited_once()
    client.disconnect.assert_awaited_once()


class CallbackScanner:
    def __init__(self, cached=None, fresh=None):
        self.discovered_devices_and_advertisement_data = cached or {}
        self.fresh = fresh or {}
        self.callback = None
        self.running = False

    def register_detection_callback(self, callback):
        self.callback = callback

        def remove():
            self.callback = None

        return remove

    async def start(self):
        self.running = True
        for device, advertisement in self.fresh.values():
            self.callback(device, advertisement)

    async def stop(self):
        self.running = False


def advertisement(service_data):
    return SimpleNamespace(service_data=service_data)


def device(address):
    return SimpleNamespace(address=address)


async def test_scan_unprovisioned_uses_only_fresh_advertisements():
    stale_uuid = bytes(16)
    fresh_uuid = bytes.fromhex("00112233445566778899aabbccddeeff")
    cached = {
        "old": (
            device("old"),
            advertisement({PROV_SERVICE: stale_uuid + bytes(2)}),
        )
    }
    fresh = {
        "new": (
            device("new"),
            advertisement({PROV_SERVICE: fresh_uuid + bytes(2)}),
        )
    }
    scanner = CallbackScanner(cached, fresh)

    found = await sphere_transport.scan_unprovisioned(
        scanner,
        timeout=0.01,
        stop_on_any=True,
        poll_interval=0.001,
        stop_after=False,
    )

    assert [(entry.device.address, entry.uuid) for entry in found] == [
        ("new", fresh_uuid)
    ]
    assert scanner.running is True
    assert scanner.callback is None


async def test_find_proxy_node_matches_fresh_network_id():
    network_id = bytes.fromhex("3ecaff672f673370")
    fresh = {
        "proxy": (
            device("proxy"),
            advertisement({PROXY_SERVICE: b"\x00" + network_id}),
        )
    }
    scanner = CallbackScanner(fresh=fresh)

    match, candidates = await sphere_transport.find_proxy_node(
        scanner,
        network_id,
        timeout=0.01,
        poll_interval=0.001,
    )

    assert match is not None
    assert match.device.address == "proxy"
    assert len(candidates) == 1
    assert scanner.running is False


def test_node_identity_verification():
    netkey = bytes(range(16))
    parameter = bytes.fromhex(
        "f8613f325ed3b78e0011223344556677"
    )

    assert sphere_transport.node_identity_matches(netkey, 0x1201, parameter)
    assert not sphere_transport.node_identity_matches(netkey, 0x1202, parameter)
    assert not sphere_transport.node_identity_matches(bytes(16), 0x1201, parameter)


async def test_find_proxy_node_matches_verified_node_identity():
    netkey = bytes(range(16))
    parameter = bytes.fromhex(
        "f8613f325ed3b78e0011223344556677"
    )
    fresh = {
        "proxy": (
            device("proxy"),
            advertisement({PROXY_SERVICE: b"\x01" + parameter}),
        )
    }
    scanner = CallbackScanner(fresh=fresh)

    match, candidates = await sphere_transport.find_proxy_node(
        scanner,
        bytes(8),
        timeout=0.01,
        poll_interval=0.001,
        identity_netkey=netkey,
        node_addresses=(0x1201,),
    )

    assert match is not None
    assert match.device.address == "proxy"
    assert len(candidates) == 1


@pytest.mark.parametrize("preference, last_failed, expected", [
    (0, None, "other"),
    (0.01, None, "target"),
    (0.01, "target", "other"),
    (0.01, "other", "target"),
])
async def test_proxy_selection_with_two_matching_advertisements(preference, last_failed, expected):
    netkey = bytes(range(16))
    identity = bytes.fromhex("f8613f325ed3b78e0011223344556677")
    network_id = bytes(8)
    scanner = CallbackScanner(fresh={
        "other": (device("other"), advertisement({PROXY_SERVICE: b"\x00" + network_id})),
        "target": (device("target"), advertisement({PROXY_SERVICE: b"\x01" + identity})),
    })

    match, _ = await sphere_transport.find_proxy_node(
        scanner, network_id, timeout=0.1, identity_netkey=netkey,
        node_addresses=(0x1201,), prefer_node_identity_for=preference,
        last_failed_address=last_failed,
    )

    assert match.device.address == expected


async def test_proxy_selection_waits_for_a_later_target_advertisement():
    network_id = bytes(8)
    identity = bytes.fromhex("f8613f325ed3b78e0011223344556677")

    class StaggeredScanner(CallbackScanner):
        async def start(self):
            await super().start()
            asyncio.get_running_loop().call_soon(
                self.callback, device("target"), advertisement({PROXY_SERVICE: b"\x01" + identity}),
            )

    scanner = StaggeredScanner(fresh={
        "other": (device("other"), advertisement({PROXY_SERVICE: b"\x00" + network_id})),
    })
    match, _ = await sphere_transport.find_proxy_node(
        scanner, network_id, timeout=0.1, poll_interval=0.001,
        identity_netkey=bytes(range(16)), node_addresses=(0x1201,),
        prefer_node_identity_for=0.05,
    )
    assert match.device.address == "target"


@pytest.mark.parametrize("last_failed", [None, "only"])
async def test_proxy_selection_can_use_the_only_available_proxy(last_failed):
    network_id = bytes(8)
    scanner = CallbackScanner(fresh={
        "only": (device("only"), advertisement({PROXY_SERVICE: b"\x00" + network_id})),
    })
    match, _ = await sphere_transport.find_proxy_node(
        scanner, network_id, timeout=0, prefer_node_identity_for=2,
        last_failed_address=last_failed,
    )
    assert match.device.address == "only"


async def test_scan_unprovisioned_reports_advertisement_before_scan_finishes():
    uuid = bytes.fromhex("00112233445566778899aabbccddeeff")
    scanner = CallbackScanner(fresh={"new": (device("new"), advertisement({PROV_SERVICE: uuid + bytes(2)}))})
    discovered = []
    task = asyncio.create_task(sphere_transport.scan_unprovisioned(
        scanner, timeout=10, on_discovered=discovered.append,
    ))
    await asyncio.sleep(0)
    assert [entry.uuid for entry in discovered] == [uuid]
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scanner.callback is None
    assert not scanner.running
