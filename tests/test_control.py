from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import OP_GENERIC_ONOFF_STATUS, generic_onoff_get
from btmesh.node import ReceivedMessage
from sphere_mesh.control import BasicController, ControlUnavailable


def status(present: int, target: int | None = None) -> ReceivedMessage:
    params = bytes([present])
    if target is not None:
        params += bytes([target, 0])
    return ReceivedMessage(
        src=0x0118,
        opcode=OP_GENERIC_ONOFF_STATUS,
        params=params,
    )


async def test_read_returns_acknowledged_state_without_tid():
    node = Mock()
    node.request = AsyncMock(return_value=status(1))
    allocate_tid = Mock()
    controller = BasicController(node, 0x0118, allocate_tid)

    result = await controller.read()

    assert result.present is True
    assert result.target is None
    assert result.effective is True
    allocate_tid.assert_not_called()
    node.request.assert_awaited_once_with(
        0x0118,
        generic_onoff_get(),
        OP_GENERIC_ONOFF_STATUS,
        timeout=5.0,
    )


async def test_set_allocates_tid_before_request():
    events = []

    class Node:
        async def request(self, *_args, **_kwargs):
            events.append("request")
            return status(0, 1)

    def allocate_tid():
        events.append("tid")
        return 37

    controller = BasicController(Node(), 0x0118, allocate_tid)

    result = await controller.set(True)

    assert events == ["tid", "request"]
    assert result.present is False
    assert result.target is True
    assert result.effective is True


async def test_toggle_reads_then_sets_opposite_effective_state():
    node = Mock()
    node.request = AsyncMock(side_effect=[status(1), status(0)])
    allocate_tid = Mock(return_value=9)
    controller = BasicController(node, 0x0118, allocate_tid)

    result = await controller.toggle()

    assert result.effective is False
    allocate_tid.assert_called_once_with()
    assert node.request.await_count == 2


async def test_timeout_reports_unavailable_and_burns_tid():
    node = Mock()
    node.request = AsyncMock(side_effect=TimeoutError)
    allocate_tid = Mock(return_value=4)
    controller = BasicController(node, 0x0118, allocate_tid)

    with pytest.raises(ControlUnavailable, match="did not acknowledge on"):
        await controller.set(True)

    allocate_tid.assert_called_once_with()


async def test_execute_rejects_unknown_command():
    controller = BasicController(Mock(), 0x0118, Mock())

    with pytest.raises(ValueError, match="unknown basic control command"):
        await controller.execute("pulse")
