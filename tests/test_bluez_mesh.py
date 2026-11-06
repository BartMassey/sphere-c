"""The BlueZ boundary uses distinct identities and routes replies safely."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dbus_fast import Message, Variant
from dbus_fast.constants import MessageType

from spherosphere.bluez_mesh import (
    APP_PATH, ELEMENT_PATH, APPLICATION, BluezAccessNode, BluezMeshError,
    BluezMeshSession, MeshApplication, MeshElement, reserve_identity,
)
from spherosphere.state import StateError, StateStore, new_state


def test_identity_reservation_is_durable_and_does_not_reuse_old_source(tmp_path):
    path = tmp_path / "state.json"
    state = new_state()
    state["seq"] = 3072
    original_keys = state["netkey"], state["appkey"]
    with StateStore(path) as store:
        identity = reserve_identity(state, store)
        assert identity["unicast"] == 0x100
        assert identity["unicast"] != state["provisioner_addr"]
        assert state["next_unicast"] == 0x101
        assert state["seq"] == 3072
        assert (state["netkey"], state["appkey"]) == original_keys
        restored = store.load_or_create()
        assert restored["bluez_mesh"] == identity
        assert reserve_identity(restored, store) == identity
        assert restored["next_unicast"] == 0x101


def test_reservation_rejects_overlapping_elements():
    state = new_state()
    state["devices"]["0x00ff"] = {"element_count": 6}
    with pytest.raises(StateError, match="overlaps"):
        reserve_identity(state, Mock())


def test_reservation_rejects_old_controller_identity():
    state = new_state()
    state["next_unicast"] = state["provisioner_addr"]
    with pytest.raises(StateError, match="no free address"):
        reserve_identity(state, Mock())


def test_joincomplete_saves_token_before_return():
    state = new_state()
    session = SimpleNamespace(identity={}, state=state, store=Mock(), _joined=asyncio.Event())
    app = MeshApplication(session)
    app.JoinComplete(123456789)
    session.store.save.assert_called_once_with(state)
    assert session.identity["token"] == 123456789
    assert session._joined.is_set()


async def test_access_waiter_requires_source_opcode_and_key_kind():
    session = SimpleNamespace(send=AsyncMock())
    node = BluezAccessNode(session)
    pending = asyncio.create_task(node.request(0x124, b"\x82\x01", 0x8204, retries=0))
    await asyncio.sleep(0)
    node.receive(0x12A, b"\x82\x04\x01", dev_key=False)
    node.receive(0x124, b"\x82\x04\x01", dev_key=True)
    node.receive(0x124, b"\x82\x03\x01", dev_key=False)
    assert not pending.done()
    node.receive(0x124, b"\x82\x04\x00", dev_key=False)
    result = await pending
    assert (result.src, result.opcode, result.params) == (0x124, 0x8204, b"\x00")
    session.send.assert_awaited_once_with(0x124, b"\x82\x01", dev_key=False, ttl=7)


async def test_access_timeout_retries_and_cleans_waiter():
    session = SimpleNamespace(send=AsyncMock())
    node = BluezAccessNode(session)
    with pytest.raises(TimeoutError):
        await node.request(0x124, b"\x82\x01", 0x8204, timeout=0.001, retries=1)
    assert session.send.await_count == 2
    assert node._waiters == []


async def test_close_fails_pending_requests_without_leaking_credentials():
    session = SimpleNamespace(send=AsyncMock())
    node = BluezAccessNode(session)
    pending = asyncio.create_task(node.request(0x124, b"\x82\x01", 0x8204))
    await asyncio.sleep(0)
    node.close()
    with pytest.raises(BluezMeshError, match="session closed"):
        await pending
    assert not node._waiters


def test_element_rejects_wrong_application_key_and_destination():
    node = Mock()
    session = SimpleNamespace(state={"appkey_index": 0, "netkey_index": 0},
                              identity={"unicast": 0x130}, node=node)
    element = MeshElement(session)
    element.MessageReceived(0x124, 1, Variant("q", 0x130), b"\x82\x04\x00")
    element.MessageReceived(0x124, 0, Variant("q", 0x131), b"\x82\x04\x00")
    node.receive.assert_not_called()
    element.MessageReceived(0x124, 0, Variant("q", 0x130), b"\x82\x04\x00")
    node.receive.assert_called_once_with(0x124, b"\x82\x04\x00", dev_key=False)


def session():
    state = new_state()
    bus = Mock()
    service = BluezMeshSession(state, Mock(), bus)
    service.node_path = "/org/bluez/mesh/node1234"
    service.call = AsyncMock(return_value=[])
    return service


async def test_send_uses_remote_addressing_for_self_configuration_and_lamp():
    service = session()
    await service.send(service.identity["unicast"], b"\x80\x26", dev_key=True)
    await service.send(0x124, b"\x80\x26", dev_key=True)
    calls = service.call.await_args_list
    assert calls[0].args[-1][2] is True
    assert calls[1].args[-1][2] is True
    await service.send(0x124, b"\x82\x01")
    assert service.call.await_args.args[2] == "Send"


async def test_configure_local_applies_single_transmission_and_no_host_beacons():
    service = session()

    async def request(dst, payload, opcode, **kwargs):
        params = {
            0x8003: b"\x00", 0x803E: b"\x00", 0x800E: b"\x07",
            0x8025: b"\x48", 0x8028: b"\x01\x48", 0x800B: b"\x00",
        }[opcode]
        return SimpleNamespace(params=params)

    async def properties():
        service.features = {"Relay": True}
        service.properties = {"Beacon": False}

    service.node.request = AsyncMock(side_effect=request)
    service.refresh_properties = properties
    await service.configure_local()
    requests = [call.args[1] for call in service.node.request.await_args_list]
    assert requests[-3:] == [b"\x80\x24\x48", b"\x80\x27\x01\x48", b"\x80\x0a\x00"]
    assert all(call.args[0] == service.identity["unicast"]
               and call.kwargs["dev_key"] for call in service.node.request.await_args_list)


@pytest.mark.parametrize("wrong_opcode,wrong_params,error", [
    (0x8025, b"\x00", "Network Transmit"),
    (0x8028, b"\x01\x00", "Relay Set"),
    (0x800B, b"\x01", "Beacon Set"),
])
async def test_configure_local_rejects_unapplied_radio_settings(wrong_opcode, wrong_params, error):
    service = session()

    async def request(dst, payload, opcode, **kwargs):
        params = {
            0x8003: b"\x00", 0x803E: b"\x00", 0x800E: b"\x07",
            0x8025: b"\x48", 0x8028: b"\x01\x48", 0x800B: b"\x00",
        }[opcode]
        return SimpleNamespace(params=wrong_params if opcode == wrong_opcode else params)

    service.node.request = AsyncMock(side_effect=request)
    with pytest.raises(BluezMeshError, match=error):
        await service.configure_local()


async def test_refresh_imports_only_current_nodes_and_requires_element_count():
    service = session()
    service.state["devices"] = {
        "0x0124": {"element_count": 6, "device_key": "11" * 16},
        "0x0100": {"element_count": 6, "device_key": "22" * 16, "superseded_by": "0x0124"},
    }
    await service.refresh_devices()
    remote_calls = [call for call in service.call.await_args_list if call.args[2] == "ImportRemoteNode"]
    assert len(remote_calls) == 2
    assert remote_calls[0].args[-1][:2] == [service.identity["unicast"], 1]
    assert remote_calls[1].args[-1][:2] == [0x124, 6]
    service.state["devices"]["0x0124"].pop("element_count")
    with pytest.raises(StateError, match="element count missing"):
        await service.refresh_devices()


def test_callback_guard_rejects_non_meshd_callers():
    service = session()
    service._owner = ":1.20"
    message = Message(path=APP_PATH, interface=APPLICATION, member="JoinComplete", signature="t", body=[123])
    message.serial = 1
    message.sender = ":1.21"
    denied = service._guard(message)
    assert denied.message_type is MessageType.ERROR
    message.sender = ":1.20"
    assert service._guard(message) is None


@pytest.mark.parametrize("body_raises", [False, True])
async def test_pause_advertising_restores_beacon_and_relay_after_body(body_raises):
    service = session()
    service.features = {"Relay": True}
    service.properties = {"Beacon": True}
    service.refresh_properties = AsyncMock()
    service.node.request = AsyncMock(side_effect=[
        SimpleNamespace(params=b"\x00\x48"),
        SimpleNamespace(params=b"\x00"),
        SimpleNamespace(params=b"\x01"),
        SimpleNamespace(params=b"\x01\x48"),
    ])

    async def work():
        async with service.pause_advertising():
            assert [call.args[1] for call in service.node.request.await_args_list] == [
                b"\x80\x27\x00\x48", b"\x80\x0a\x00",
            ]
            if body_raises:
                raise RuntimeError("enrollment failed")

    if body_raises:
        with pytest.raises(RuntimeError, match="enrollment failed"):
            await work()
    else:
        await work()

    assert [call.args[1] for call in service.node.request.await_args_list] == [
        b"\x80\x27\x00\x48", b"\x80\x0a\x00",
        b"\x80\x0a\x01", b"\x80\x27\x01\x48",
    ]
    assert all(call.kwargs["dev_key"] for call in service.node.request.await_args_list)
    assert service.refresh_properties.await_count == 2
    assert not service._lost.is_set()


async def test_pause_advertising_attempts_relay_restore_after_beacon_restore_failure():
    service = session()
    service.features = {"Relay": True}
    service.properties = {"Beacon": True}
    service.refresh_properties = AsyncMock()
    service.node.request = AsyncMock(side_effect=[
        SimpleNamespace(params=b"\x00\x48"),
        SimpleNamespace(params=b"\x00"),
        BluezMeshError("restore Beacon", "simulated failure"),
        SimpleNamespace(params=b"\x01\x48"),
    ])

    with pytest.raises(BluezMeshError, match="restore Beacon"):
        async with service.pause_advertising():
            pass

    assert service.node.request.await_count == 4
    assert service.node.request.await_args.args[1] == b"\x80\x27\x01\x48"
    assert service._lost.is_set()
