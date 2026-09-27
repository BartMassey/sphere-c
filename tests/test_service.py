from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import OP_GENERIC_ONOFF_STATUS, OP_LIGHT_LIGHTNESS_STATUS
from btmesh.node import ReceivedMessage
from sphere_mesh.api import ApiRequestError
from sphere_mesh.daemon import DP_NAMES, SphereRuntime
from sphere_mesh.vendor import OP_TUYA_DP_DATA


class Store:
    def allocate_tid(self, state):
        tid = state["tid"]
        state["tid"] = (tid + 1) & 0xFF
        return tid


def test_semantic_names_cover_complete_product_schema():
    assert DP_NAMES == {
        1: "switch_led",
        2: "work_mode",
        3: "bright_value",
        5: "colour_data",
        6: "scene_data",
        7: "countdown",
        8: "music_data",
        33: "power_memory",
        37: "rtc_timer",
        38: "timer_report",
        101: "countdown_2",
        102: "colour_mode",
        103: "battery_percentage",
        104: "sleep_switch",
        105: "game_flag",
        106: "factory_mode_reset",
        107: "water_mode",
    }


def message(opcode, params):
    return ReceivedMessage(src=0x0118, opcode=opcode, params=params)


def runtime(node=None, *, connected=True):
    session = None if not connected else SimpleNamespace(node=node or Mock())
    supervisor = SimpleNamespace(
        session=session,
        generation=3,
        last_failure=None,
    )
    state = {
        "tid": 2,
        "netkey": "secret",
        "appkey": "secret",
        "composition": {
            "elements": [
                {
                    "address": 0x0118,
                    "sig_models": [0x1300, 0x1307],
                    "vendor_models": [
                        {"company_id": 0x07D0, "model_id": 0x0004}
                    ],
                }
            ]
        },
    }
    return SphereRuntime(supervisor, state, Store(), 0x0118), state


async def test_health_is_available_while_disconnected_and_hides_keys():
    service, _ = runtime(connected=False)

    result = await service.handle("health", {})

    assert result == {
        "status": "connecting",
        "connected": False,
        "generation": 3,
        "device": "0x0118",
        "last_failure": None,
    }
    assert "key" not in repr(result).lower()


async def test_status_reports_saved_capabilities_while_disconnected():
    service, _ = runtime(connected=False)
    result = await service.handle("status", {})
    assert result["connected"] is False
    assert result["capabilities"] == {
        "control": False,
        "lighting": {
            "lightness": True,
            "temperature": False,
            "hsl": True,
            "xyl": False,
        },
        "vendor_read": True,
        "effects": True,
        "countdown": True,
    }
    assert "key" not in repr(result).lower()


async def test_control_returns_device_acknowledged_state():
    node = Mock()
    node.request = AsyncMock(
        return_value=message(OP_GENERIC_ONOFF_STATUS, b"\x00\x01\x00")
    )
    service, state = runtime(node)

    result = await service.handle("control", {"command": "on"})

    assert result["command"] == "on"
    assert result["state"] == {
        "present": False,
        "target": True,
        "remaining_time": 0,
        "effective": True,
    }
    assert state["tid"] == 3
    assert result["verification"] == {
        "transport_acknowledged": True,
        "response_reported_value": True,
        "independent_readback": False,
        "visually_verified": False,
    }


async def test_lighting_returns_typed_state():
    node = Mock()
    node.request = AsyncMock(
        return_value=message(OP_LIGHT_LIGHTNESS_STATUS, b"\x34\x12")
    )
    service, _ = runtime(node)

    result = await service.handle(
        "lighting", {"command": "lightness"}
    )

    assert result == {
        "command": "lightness",
        "model": "lightness",
        "state": {
            "present": 0x1234,
            "target": None,
            "remaining_time": None,
        },
        "verification": {
            "transport_acknowledged": True,
            "response_reported_value": True,
            "independent_readback": True,
            "visually_verified": False,
        },
    }


async def test_vendor_read_returns_typed_and_raw_datapoints():
    node = Mock()
    node.received = deque()
    response = message(
        OP_TUYA_DP_DATA,
        bytes.fromhex("01 02 04 02 66 04 1f"),
    )

    async def request(*args, **kwargs):
        node.received.append(response)
        return response

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)

    result = await service.handle("vendor.read", {"dp_ids": [2, 102]})

    assert result == {
        "command_type": 1,
        "requested_dp_ids": [2, 102],
        "missing_dp_ids": [],
        "datapoints": [
            {"id": 2, "name": "work_mode", "type": "enum", "value": 2, "raw": "02"},
            {"id": 102, "name": "colour_mode", "type": "enum", "value": 31, "raw": "1f"},
        ],
        "verification": {
            "transport_acknowledged": True,
            "response_reported_value": True,
            "independent_readback": True,
            "visually_verified": False,
        },
    }


async def test_effect_list_is_available_without_bluetooth():
    service, _ = runtime(connected=False)
    result = await service.handle("effect.list", {})
    assert result["effects"][2] == {
        "name": "colour_2", "canonical": "colour_2", "slug": "aurora",
        "value": 2, "label": "Aurora", "visible": True,
    }


async def test_effect_get_returns_current_vendor_snapshot():
    node = Mock()
    node.received = deque()
    response = message(OP_TUYA_DP_DATA, bytes.fromhex("01660407"))

    async def request(*args, **kwargs):
        node.received.append(response)
        return response

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)
    result = await service.handle("effect.get", {})
    assert result["effect"] == {
        "name": "colour_7", "canonical": "colour_7", "slug": "color-wheel",
        "value": 7, "label": "Color Wheel", "visible": True,
    }
    assert result["snapshot"]["missing_dp_ids"] == [2]


async def test_effect_set_requires_visible_name_and_reports_ack():
    node = Mock()
    node.received = deque()
    ack = message(OP_TUYA_DP_DATA, b"\x01")

    async def request(*args, **kwargs):
        node.received.append(ack)
        return ack

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)
    result = await service.handle("effect.set", {"name": "colour_2"})
    assert result["requested_effect"] == "colour_2"
    assert result["acknowledged"] is True
    assert result["reported_effect"] is None
    assert result["verification"] == {
        "transport_acknowledged": True,
        "response_reported_value": False,
        "independent_readback": False,
        "visually_verified": False,
    }
    node.request.assert_awaited_once_with(
        0x0118, bytes.fromhex("c9d00701660402"),
        OP_TUYA_DP_DATA, timeout=5.0, retries=0,
    )


@pytest.mark.parametrize("method", ["effect.set", "effect.select", "effect.activate"])
async def test_effect_write_accepts_slug_and_reports_canonical_name(method):
    node = Mock()
    node.received = deque()
    ack = message(OP_TUYA_DP_DATA, b"\x01")

    async def request(*args, **kwargs):
        node.received.append(ack)
        return ack

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)
    result = await service.handle(method, {"name": "aurora"})
    assert result["requested_effect"] == "colour_2"
    assert result["acknowledged"] is True
    assert node.request.await_args_list[-1].args[1] == bytes.fromhex(
        "c9d00701660402"
    )
    assert node.request.await_count == (2 if method == "effect.activate" else 1)


async def test_effect_set_rejects_a_contradictory_echo():
    node = Mock()
    node.received = deque()
    response = message(OP_TUYA_DP_DATA, bytes.fromhex("01660407"))

    async def request(*args, **kwargs):
        node.received.append(response)
        return response

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("effect.set", {"name": "colour_2"})
    assert failure.value.code == "protocol_error"


async def test_effect_activate_writes_mode_before_effect():
    node = Mock()
    node.received = deque()
    ack = message(OP_TUYA_DP_DATA, b"\x01")

    async def request(*args, **kwargs):
        node.received.append(ack)
        return ack

    node.request = AsyncMock(side_effect=request)
    service, _ = runtime(node)
    result = await service.handle("effect.activate", {"name": "colour_2"})
    assert result["acknowledged"] is True
    assert node.request.await_count == 2
    assert [call.args[1] for call in node.request.await_args_list] == [
        bytes.fromhex("c9d00701020401"),
        bytes.fromhex("c9d00701660402"),
    ]
    assert all(call.kwargs["retries"] == 0 for call in node.request.await_args_list)


async def test_effect_activate_stops_when_mode_write_times_out():
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(side_effect=TimeoutError)
    service, _ = runtime(node)
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("effect.activate", {"name": "colour_2"})
    assert failure.value.code == "unavailable"
    assert node.request.await_count == 1


async def test_effect_activate_reports_partial_failure_after_mode_ack():
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(side_effect=[
        message(OP_TUYA_DP_DATA, b"\x01"), TimeoutError(),
    ])
    service, _ = runtime(node)
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("effect.activate", {"name": "aurora"})
    assert failure.value.code == "unavailable"
    assert failure.value.details == {
        "completed_steps": ["mode_transport_acknowledged"],
        "failed_step": "effect",
        "effect_write_attempted": True,
        "result_uncertain": True,
    }
    assert node.request.await_count == 2


async def test_effect_activate_rejects_wrong_mode_without_effect_write():
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(return_value=message(
        OP_TUYA_DP_DATA, bytes.fromhex("01020400")
    ))
    service, _ = runtime(node)
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("effect.activate", {"name": "aurora"})
    assert failure.value.code == "protocol_error"
    assert node.request.await_count == 1


@pytest.mark.parametrize(
    "method,params,expected",
    [
        ("countdown.set", {"seconds": 60}, 60),
        ("countdown.cancel", {}, 0),
    ],
)
async def test_countdown_write_accepts_empty_ack(method, params, expected):
    node = Mock()
    node.received = deque()
    ack = message(OP_TUYA_DP_DATA, b"\x01")
    node.request = AsyncMock(return_value=ack)
    service, _ = runtime(node)
    result = await service.handle(method, params)
    assert result["requested_seconds"] == expected
    assert result["acknowledged"] is True
    assert result["reported_seconds"] is None
    node.request.assert_awaited_once_with(
        0x0118, bytes.fromhex("c9d007016502") + expected.to_bytes(4, "big"),
        OP_TUYA_DP_DATA, timeout=5.0, retries=0,
    )


async def test_countdown_get_extracts_dp_101_from_snapshot():
    node = Mock()
    node.received = deque()
    response = message(
        OP_TUYA_DP_DATA, bytes.fromhex("0102040165020000003c")
    )
    node.request = AsyncMock(return_value=response)
    service, _ = runtime(node)
    result = await service.handle("countdown.get", {})
    assert result["seconds"] == 60
    assert result["snapshot"]["requested_dp_ids"] == [101]
    node.request.assert_awaited_once_with(
        0x0118, bytes.fromhex("ccd007010165"), OP_TUYA_DP_DATA,
        timeout=5.0,
    )


@pytest.mark.parametrize(
    "response",
    [
        "0165020000003d",
        "01650101",
        "0165020000a8c1",
        "0107020000003c",
    ],
)
async def test_countdown_write_rejects_bad_echo(response):
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(
        return_value=message(OP_TUYA_DP_DATA, bytes.fromhex(response))
    )
    service, _ = runtime(node)
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("countdown.set", {"seconds": 60})
    assert failure.value.code == "protocol_error"


@pytest.mark.parametrize(
    ("method", "params", "code"),
    [
        ("wat", {}, "unknown_method"),
        ("health", {"extra": True}, "invalid_params"),
        ("status", {"extra": True}, "invalid_params"),
        ("control", {"command": "pulse"}, "invalid_params"),
        ("lighting", {"command": "rgb"}, "invalid_params"),
        ("vendor.read", {"dp_ids": []}, "invalid_params"),
        ("vendor.read", {"dp_ids": [2, 2]}, "invalid_params"),
        ("vendor.read", {"dp_ids": [256]}, "invalid_params"),
        ("effect.get", {"name": "flame"}, "invalid_params"),
        ("effect.list", {"name": "flame"}, "invalid_params"),
        ("effect.set", {"name": "colour_5"}, "invalid_params"),
        ("effect.set", {"name": "colour_32"}, "invalid_params"),
        ("effect.set", {"name": 2}, "invalid_params"),
        ("countdown.get", {"seconds": 2}, "invalid_params"),
        ("countdown.cancel", {"seconds": 0}, "invalid_params"),
        ("countdown.set", {"seconds": True}, "invalid_params"),
        ("countdown.set", {"seconds": 43201}, "invalid_params"),
        ("countdown.set", {"seconds": -1}, "invalid_params"),
    ],
)
async def test_runtime_rejects_invalid_requests(method, params, code):
    service, _ = runtime()

    with pytest.raises(ApiRequestError) as failure:
        await service.handle(method, params)

    assert failure.value.code == code


async def test_commands_fail_fast_while_disconnected():
    service, _ = runtime(connected=False)

    with pytest.raises(ApiRequestError) as failure:
        await service.handle("control", {"command": "read"})

    assert failure.value.code == "unavailable"


async def test_runtime_resumes_commands_after_reconnect():
    node = Mock()
    node.request = AsyncMock(return_value=message(
        OP_GENERIC_ONOFF_STATUS, b"\x01"
    ))
    service, _ = runtime(node)
    session = service._supervisor.session
    service._supervisor.session = None
    service._supervisor.last_failure = OSError("link dropped")

    assert (await service.handle("status", {}))["connected"] is False
    with pytest.raises(ApiRequestError) as failure:
        await service.handle("control", {"command": "read"})
    assert failure.value.code == "unavailable"
    node.request.assert_not_awaited()

    service._supervisor.session = session
    service._supervisor.generation += 1
    assert (await service.handle("status", {}))["generation"] == 4
    result = await service.handle("control", {"command": "read"})
    assert result["state"]["present"] is True
    node.request.assert_awaited_once()
