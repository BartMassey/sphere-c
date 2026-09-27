from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import (
    OP_LIGHT_CTL_TEMPERATURE_STATUS,
    OP_LIGHT_LIGHTNESS_STATUS,
    light_ctl_temperature_set,
    light_lightness_get,
)
from btmesh.node import ReceivedMessage
from sphere_mesh.access import OP_LIGHT_HSL_STATUS
from sphere_mesh.lighting import (
    LightingController,
    LightingUnavailable,
    LightingUnsupported,
    model_element,
    parse_lighting_command,
)


COMPOSITION = {
    "elements": [
        {"address": 0x0118, "sig_models": [0x1300, 0x1307]},
        {"address": 0x0119, "sig_models": [0x1306]},
    ]
}


def message(opcode: int, params: bytes) -> ReceivedMessage:
    return ReceivedMessage(src=0x0118, opcode=opcode, params=params)


def test_model_element_resolves_owning_element_and_rejects_absent_model():
    assert model_element(COMPOSITION, 0x1306) == 0x0119

    with pytest.raises(LightingUnsupported, match="0x130c"):
        model_element(COMPOSITION, 0x130C)


async def test_read_lightness_uses_model_element_without_allocating_tid():
    node = Mock()
    node.request = AsyncMock(
        return_value=message(OP_LIGHT_LIGHTNESS_STATUS, b"\x34\x12")
    )
    allocate_tid = Mock()
    controller = LightingController(node, COMPOSITION, allocate_tid)

    result = await controller.read_lightness()

    assert result.present == 0x1234
    assert result.target is None
    allocate_tid.assert_not_called()
    node.request.assert_awaited_once_with(
        0x0118,
        light_lightness_get(),
        OP_LIGHT_LIGHTNESS_STATUS,
        timeout=5.0,
    )


async def test_temperature_set_allocates_tid_before_request():
    events = []

    class Node:
        async def request(self, address, payload, opcode, *, timeout):
            events.append(("request", address, payload, opcode, timeout))
            return message(
                OP_LIGHT_CTL_TEMPERATURE_STATUS,
                b"\x88\x13\xfe\xff",
            )

    def allocate_tid():
        events.append(("tid",))
        return 9

    controller = LightingController(Node(), COMPOSITION, allocate_tid)

    result = await controller.set_temperature(5000, -2)

    assert result.present == 5000
    assert result.delta_uv == -2
    assert events == [
        ("tid",),
        (
            "request",
            0x0119,
            light_ctl_temperature_set(5000, -2, 9),
            OP_LIGHT_CTL_TEMPERATURE_STATUS,
            5.0,
        ),
    ]


async def test_hsl_set_returns_acknowledged_values():
    node = Mock()
    node.request = AsyncMock(
        return_value=message(
            OP_LIGHT_HSL_STATUS,
            b"\x01\x00\x02\x00\x03\x00\x04",
        )
    )
    controller = LightingController(node, COMPOSITION, Mock(return_value=5))

    result = await controller.set_hsl(1, 2, 3)

    assert result.lightness == 1
    assert result.hue == 2
    assert result.saturation == 3
    assert result.remaining_time == 4


async def test_unsupported_xyl_neither_allocates_tid_nor_transmits():
    node = Mock()
    node.request = AsyncMock()
    allocate_tid = Mock()
    controller = LightingController(node, COMPOSITION, allocate_tid)

    with pytest.raises(LightingUnsupported, match="0x130c"):
        await controller.set_xyl(1, 2, 3)

    allocate_tid.assert_not_called()
    node.request.assert_not_awaited()


async def test_timeout_reports_unavailable_and_burns_tid():
    node = Mock()
    node.request = AsyncMock(side_effect=TimeoutError)
    allocate_tid = Mock(return_value=7)
    controller = LightingController(node, COMPOSITION, allocate_tid)

    with pytest.raises(LightingUnavailable, match="Lightness Set"):
        await controller.set_lightness(123)

    allocate_tid.assert_called_once_with()


@pytest.mark.parametrize(
    ("raw", "model", "values"),
    [
        ("lightness", "lightness", None),
        ("lightness=0xffff", "lightness", (65535,)),
        ("temperature=5000", "temperature", (5000,)),
        ("hsl=1,2,3", "hsl", (1, 2, 3)),
        ("xyl=4,5,6", "xyl", (4, 5, 6)),
    ],
)
def test_parse_lighting_command(raw, model, values):
    assert parse_lighting_command(raw).model == model
    assert parse_lighting_command(raw).values == values


def test_lighting_command_identifies_required_server_model():
    assert parse_lighting_command("lightness").server_model == 0x1300
    assert parse_lighting_command("temperature").server_model == 0x1306
    assert parse_lighting_command("hsl").server_model == 0x1307
    assert parse_lighting_command("xyl").server_model == 0x130C


@pytest.mark.parametrize(
    "raw",
    [
        "rgb",
        "hsl=1,2",
        "xyl=1,nope,3",
        "lightness=65536",
        "temperature=799",
    ],
)
def test_parse_lighting_command_rejects_invalid_input(raw):
    with pytest.raises(ValueError):
        parse_lighting_command(raw)
