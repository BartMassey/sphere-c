import pytest

from btmesh.access import AccessError
from sphere_mesh.access import (
    MODEL_LIGHT_CTL_TEMPERATURE_SERVER,
    MODEL_LIGHT_HSL_SERVER,
    MODEL_LIGHT_XYL_SERVER,
    config_network_transmit_set,
    light_hsl_get,
    light_hsl_set,
    light_xyl_get,
    light_xyl_set,
    parse_light_hsl_status,
    parse_light_xyl_status,
)


def test_network_transmit_set_matches_sphere_configuration():
    assert config_network_transmit_set(5, 2) == bytes.fromhex("802415")


@pytest.mark.parametrize("count", [-1, 8])
def test_network_transmit_set_rejects_invalid_count(count):
    with pytest.raises(AccessError, match="count"):
        config_network_transmit_set(count, 2)


@pytest.mark.parametrize("steps", [-1, 32])
def test_network_transmit_set_rejects_invalid_interval(steps):
    with pytest.raises(AccessError, match="interval"):
        config_network_transmit_set(5, steps)


def test_step4_model_identifiers():
    assert MODEL_LIGHT_CTL_TEMPERATURE_SERVER == 0x1306
    assert MODEL_LIGHT_HSL_SERVER == 0x1307
    assert MODEL_LIGHT_XYL_SERVER == 0x130C


def test_hsl_messages_match_assigned_opcodes_and_layout():
    assert light_hsl_get() == bytes.fromhex("826d")
    assert light_hsl_set(0x1234, 0x5678, 0x9ABC, 0xDE) == bytes.fromhex(
        "827634127856bc9ade"
    )
    assert light_hsl_set(
        0x1234, 0x5678, 0x9ABC, 0xDE, ack=False
    ) == bytes.fromhex("827734127856bc9ade")


def test_parse_hsl_status_settled_and_transitioning():
    settled = parse_light_hsl_status(bytes.fromhex("827834127856bc9a"))
    moving = parse_light_hsl_status(bytes.fromhex("827834127856bc9a21"))

    assert settled == (0x1234, 0x5678, 0x9ABC, None)
    assert moving == (0x1234, 0x5678, 0x9ABC, 0x21)


def test_xyl_messages_match_assigned_opcodes_and_layout():
    assert light_xyl_get() == bytes.fromhex("8283")
    assert light_xyl_set(0x1234, 0x5678, 0x9ABC, 0xDE) == bytes.fromhex(
        "828434127856bc9ade"
    )
    assert light_xyl_set(
        0x1234, 0x5678, 0x9ABC, 0xDE, ack=False
    ) == bytes.fromhex("828534127856bc9ade")


def test_parse_xyl_status_settled_and_transitioning():
    settled = parse_light_xyl_status(bytes.fromhex("828634127856bc9a"))
    moving = parse_light_xyl_status(bytes.fromhex("828634127856bc9a21"))

    assert settled == (0x1234, 0x5678, 0x9ABC, None)
    assert moving == (0x1234, 0x5678, 0x9ABC, 0x21)


@pytest.mark.parametrize(
    "builder,args",
    [
        (light_hsl_set, (-1, 0, 0, 0)),
        (light_hsl_set, (0, 0x10000, 0, 0)),
        (light_xyl_set, (0, 0, 0x10000, 0)),
        (light_xyl_set, (0, 0, 0, 0x100)),
    ],
)
def test_color_set_rejects_out_of_range_fields(builder, args):
    with pytest.raises(AccessError):
        builder(*args)


@pytest.mark.parametrize(
    "parser,payload",
    [
        (parse_light_hsl_status, bytes.fromhex("827800")),
        (parse_light_hsl_status, bytes.fromhex("828634127856bc9a")),
        (parse_light_xyl_status, bytes.fromhex("828600")),
        (parse_light_xyl_status, bytes.fromhex("827834127856bc9a")),
    ],
)
def test_color_status_rejects_wrong_opcode_or_length(parser, payload):
    with pytest.raises(AccessError):
        parser(payload)
