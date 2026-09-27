from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import OP_GENERIC_ONOFF_STATUS, OP_LIGHT_LIGHTNESS_STATUS
from btmesh.node import ReceivedMessage
from sphere_mesh.api import ApiRequestError
from sphere_mesh.daemon import SphereRuntime


class Store:
    def allocate_tid(self, state):
        tid = state["tid"]
        state["tid"] = (tid + 1) & 0xFF
        return tid


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
                {"address": 0x0118, "sig_models": [0x1300, 0x1307]}
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
    }


@pytest.mark.parametrize(
    ("method", "params", "code"),
    [
        ("wat", {}, "unknown_method"),
        ("health", {"extra": True}, "invalid_params"),
        ("control", {"command": "pulse"}, "invalid_params"),
        ("lighting", {"command": "rgb"}, "invalid_params"),
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
