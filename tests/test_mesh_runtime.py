"""Multi-device routing checks with no Bluetooth adapter or D-Bus access."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import OP_GENERIC_ONOFF_STATUS, OP_LIGHT_LIGHTNESS_STATUS
from btmesh.node import ReceivedMessage
from sphere_mesh.api import ApiRequestError
from sphere_mesh.daemon import MeshRuntime
from sphere_mesh.state import new_state
from sphere_mesh.vendor import OP_TUYA_DP_DATA


def composition(address, *, vendor=True, lightness=True):
    return {"elements": [{
        "address": address,
        "sig_models": [0x1000, *([0x1300] if lightness else [])],
        "vendor_models": ([{"company_id": 0x07D0, "model_id": 0x0004}]
                          if vendor else []),
    }]}


def mesh_runtime():
    state = new_state()
    state["active_device"] = "0x012a"
    state["composition"] = composition(0x012a)
    for key, name in (("0x012a", "ARI"), ("0x0124", "BEN")):
        state["devices"][key] = {
            "name": name, "device_key": "11" * 16,
            "official_config_complete": True, "element_count": 6,
            "composition": composition(int(key, 16)),
        }
    node = SimpleNamespace(received=deque(), request=AsyncMock())
    supervisor = SimpleNamespace(
        session=SimpleNamespace(node=node, links=[{"address": "radio"}]),
        generation=1, last_failure=None, suspend=AsyncMock(), start=Mock(),
    )
    store = Mock()
    store.allocate_tid.return_value = 0
    return MeshRuntime(supervisor, state, store), node, state, store


async def test_explicit_names_and_addresses_route_independent_power_reads():
    runtime, node, _, _ = mesh_runtime()

    async def reply(address, _payload, opcode, **_kwargs):
        return ReceivedMessage(src=address, opcode=opcode,
                               params=b"\x01" if address == 0x012a else b"\x00")

    node.request.side_effect = reply
    ari = await runtime.handle("control", {"device": "ARI", "command": "read"})
    ben = await runtime.handle("control", {"device": "0x0124", "command": "read"})

    assert ari["device"] == "0x012a" and ari["state"]["present"] is True
    assert ben["device"] == "0x0124" and ben["state"]["present"] is False
    assert [call.args[0] for call in node.request.await_args_list] == [0x012a, 0x0124]


async def test_lighting_uses_selected_device_composition_not_active_root():
    runtime, node, state, _ = mesh_runtime()
    state["devices"]["0x0124"]["composition"] = composition(0x0125)
    node.request.return_value = ReceivedMessage(
        src=0x0125, opcode=OP_LIGHT_LIGHTNESS_STATUS, params=b"\x34\x12",
    )

    result = await runtime.handle("lighting", {"device": "BEN", "command": "lightness"})

    assert result["state"]["present"] == 0x1234
    assert node.request.await_args.args[0] == 0x0125
    # A missing model on BEN must not inherit ARI's model.
    state["devices"]["0x0124"]["composition"] = composition(0x0124, lightness=False)
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("lighting", {"device": "BEN", "command": "lightness"})
    assert failure.value.code == "unsupported"
    assert node.request.await_count == 1


async def test_vendor_mode_cache_is_per_device_and_expires_on_reconnect():
    runtime, node, _, _ = mesh_runtime()
    responses = iter([
        (0x012a, "01020401660402"),
        (0x0124, "01020400660407"),
        (0x012a, "01660402"),
        (0x012a, "01660402"),
    ])

    async def reply(address, *_args, **_kwargs):
        expected, payload = next(responses)
        assert address == expected
        result = ReceivedMessage(src=address, opcode=OP_TUYA_DP_DATA,
                                 params=bytes.fromhex(payload))
        node.received.append(result)
        return result

    node.request.side_effect = reply
    assert (await runtime.handle("effect.get", {"device": "ARI"}))["work_mode"] == 1
    assert (await runtime.handle("effect.get", {"device": "BEN"}))["work_mode"] == 0
    ari = await runtime.handle("effect.get", {"device": "ARI"})
    assert ari["work_mode"] == 1 and ari["work_mode_source"] == "cached"
    runtime.supervisor.generation += 1
    assert (await runtime.handle("effect.get", {"device": "ARI"}))["work_mode"] is None


async def test_untargeted_request_is_rejected_when_multiple_members_exist():
    runtime, node, _, _ = mesh_runtime()
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("control", {"command": "on"})
    assert failure.value.code == "device_required"
    node.request.assert_not_awaited()


async def test_disabled_enrollment_reports_unavailability_without_touching_nodes():
    runtime, node, _, _ = mesh_runtime()
    runtime.enrollment = None
    status = await runtime.handle("enrollment.status", {})
    assert status["active"] is False
    assert status["available"] is False
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("enrollment.start", {"duration": 120})
    assert failure.value.code == "unavailable"
    assert "experimental-enrollment" in str(failure.value)
    node.request.assert_not_awaited()


async def test_inventory_excludes_superseded_and_hides_credentials():
    runtime, _, state, _ = mesh_runtime()
    state["devices"]["0x0100"] = {
        "name": "ARI", "device_key": "old-private-key", "superseded_by": "0x012a",
    }
    result = await runtime.handle("device.list", {})
    assert {entry["name"] for entry in result["devices"]} == {"ARI", "BEN"}
    assert "key" not in repr(result).lower()
    assert state["netkey"] not in repr(result)
    assert state["appkey"] not in repr(result)
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("status", {"device": "0x0100"})
    assert failure.value.code == "unknown_device"


async def test_rename_persists_unique_alias_without_changing_network_identity():
    runtime, _, state, store = mesh_runtime()
    before = (state["netkey"], state["appkey"], state["seq"])
    result = await runtime.handle("device.rename", {"device": "BEN", "name": "  BOB  "})
    assert result == {"device": "0x0124", "name": "BOB"}
    store.save.assert_called_once_with(state)
    assert runtime._target("BOB") == "0x0124"
    assert before == (state["netkey"], state["appkey"], state["seq"])
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("device.rename", {"device": "BOB", "name": "ARI"})
    assert failure.value.code == "invalid_params"


async def test_two_devices_and_enrollment_share_one_command_lock():
    runtime, node, _, _ = mesh_runtime()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def reply(address, *_args, **_kwargs):
        calls.append(address)
        entered.set()
        await release.wait()
        return ReceivedMessage(src=address, opcode=OP_GENERIC_ONOFF_STATUS, params=b"\x00")

    node.request.side_effect = reply
    first = asyncio.create_task(runtime.handle("control", {"device": "ARI", "command": "read"}))
    await entered.wait()

    async def enrollment():
        async with runtime.exclusive():
            assert calls == [0x012a]
            assert runtime.supervisor.suspend.await_count == 1

    enroll = asyncio.create_task(enrollment())
    second = asyncio.create_task(runtime.handle("control", {"device": "BEN", "command": "read"}))
    await asyncio.sleep(0)
    assert calls == [0x012a]
    runtime.supervisor.suspend.assert_not_awaited()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, enroll, second), 1)
    assert calls == [0x012a, 0x0124]
    runtime.supervisor.start.assert_called_once()


async def test_incomplete_configuration_is_visible_but_not_controllable():
    runtime, node, state, _ = mesh_runtime()
    state["devices"]["0x0124"]["official_config_complete"] = False
    assert (await runtime.handle("status", {"device": "BEN"}))["device"] == "0x0124"
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("control", {"device": "BEN", "command": "on"})
    assert failure.value.code == "unavailable"
    node.request.assert_not_awaited()


async def test_enrollment_methods_dispatch_and_reject_invalid_arguments():
    runtime, _, _, _ = mesh_runtime()
    manager = SimpleNamespace(start=Mock(return_value={"active": True}),
                              status=Mock(return_value={"active": True}),
                              stop=AsyncMock(return_value={"active": False}))
    runtime.enrollment = manager
    assert await runtime.handle("enrollment.start", {"duration": 30}) == {"active": True}
    manager.start.assert_called_once_with(30)
    assert await runtime.handle("enrollment.status", {}) == {"active": True}
    assert await runtime.handle("enrollment.stop", {}) == {"active": False}
    manager.stop.assert_awaited_once()
    for params in ({"duration": True}, {"duration": 0}, {"duration": float("nan")}, {"other": 3}):
        with pytest.raises(ApiRequestError) as failure:
            await runtime.handle("enrollment.start", params)
        assert failure.value.code == "invalid_params"
    manager.start.side_effect = ValueError("enrollment is already active")
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("enrollment.start", {})
    assert failure.value.code == "invalid_params"


async def test_queued_request_rejects_superseded_target_without_redirecting():
    runtime, node, state, _ = mesh_runtime()
    await runtime.command_lock.acquire()
    queued = asyncio.create_task(runtime.handle(
        "control", {"device": "ARI", "command": "on"},
    ))
    try:
        await asyncio.sleep(0)
        assert not queued.done()
        assert "0x012a" in runtime._runtimes
        state["devices"]["0x012a"]["superseded_by"] = "0x0131"
        state["devices"]["0x0131"] = {
            "name": "ARI", "official_config_complete": True,
            "composition": composition(0x0131),
        }
    finally:
        runtime.command_lock.release()
    with pytest.raises(ApiRequestError) as failure:
        await asyncio.wait_for(queued, 1)
    assert failure.value.code == "unknown_device"
    node.request.assert_not_awaited()


@pytest.mark.parametrize("body_raises", [False, True])
async def test_bluez_enrollment_keeps_relay_attached_and_refreshes_devices(body_raises):
    runtime, _, state, _ = mesh_runtime()
    session = runtime.supervisor.session
    session.properties = {"IvIndex": state["iv_index"], "IvUpdate": False}
    session.refresh_properties = AsyncMock()
    session.refresh_devices = AsyncMock()
    advertising = []

    @asynccontextmanager
    async def pause_advertising():
        advertising.append("paused")
        try:
            yield
        finally:
            session.refresh_devices.assert_awaited_once()
            advertising.append("restored")

    session.pause_advertising = pause_advertising

    async def enroll():
        async with runtime.exclusive():
            assert runtime.command_lock.locked()
            assert runtime.supervisor.session is session
            assert advertising == ["paused"]
            session.refresh_properties.assert_awaited_once()
            session.refresh_devices.assert_not_awaited()
            if body_raises:
                raise RuntimeError("commissioning failed")

    if body_raises:
        with pytest.raises(RuntimeError, match="commissioning failed"):
            await enroll()
    else:
        await enroll()
    session.refresh_devices.assert_awaited_once()
    assert advertising == ["paused", "restored"]
    runtime.supervisor.suspend.assert_not_awaited()
    runtime.supervisor.start.assert_not_called()
    assert not runtime.command_lock.locked()


@pytest.mark.parametrize("properties", [
    {"IvIndex": 0, "IvUpdate": True},
    {"IvIndex": 1, "IvUpdate": False},
])
async def test_bluez_enrollment_rejects_changed_iv_state_before_work(properties):
    runtime, _, _, _ = mesh_runtime()
    session = runtime.supervisor.session
    session.properties = properties
    session.refresh_properties = AsyncMock()
    session.refresh_devices = AsyncMock()
    entered = False
    with pytest.raises(ApiRequestError) as failure:
        async with runtime.exclusive():
            entered = True
    assert failure.value.code == "unavailable"
    assert not entered
    session.refresh_properties.assert_awaited_once()
    session.refresh_devices.assert_not_awaited()
    runtime.supervisor.suspend.assert_not_awaited()
    runtime.supervisor.start.assert_not_called()
    assert not runtime.command_lock.locked()

async def test_forget_preserves_allocation_and_selects_remaining_device():
    runtime, node, state, store = mesh_runtime()
    before = (state["seq"], state["devices"]["0x012a"]["device_key"])
    result = await runtime.handle("device.forget", {"device": "ARI"})
    assert result == {"device": "0x012a", "forgotten": True}
    assert state["devices"]["0x012a"]["forgotten"]
    assert (state["seq"], state["devices"]["0x012a"]["device_key"]) == before
    assert state["active_device"] == "0x0124"
    assert state["composition"] == state["devices"]["0x0124"]["composition"]
    assert [item["name"] for item in runtime.inventory()["devices"]] == ["BEN"]
    store.save.assert_called_once_with(state)
    with pytest.raises(ApiRequestError) as failure:
        await runtime.handle("status", {"device": "ARI"})
    assert failure.value.code == "unknown_device"
    node.request.assert_not_awaited()


async def test_device_management_rejects_active_enrollment():
    runtime, _, _, _ = mesh_runtime()
    runtime.enrollment = SimpleNamespace(status=Mock(return_value={"active": True}))
    for method, params in (("device.forget", {"device": "ARI"}),
                           ("device.rename", {"device": "ARI", "name": "TEST"})):
        with pytest.raises(ApiRequestError) as failure:
            await runtime.handle(method, params)
        assert failure.value.code == "unavailable"
