"""Sphere-required Mesh messages absent from btmesh 0.10.3."""

from typing import NamedTuple

from btmesh.access import AccessError, encode_opcode, parse_access

OP_CONFIG_NETWORK_TRANSMIT_SET = 0x8024
OP_CONFIG_NETWORK_TRANSMIT_STATUS = 0x8025

OP_LIGHT_HSL_GET = 0x826D
OP_LIGHT_HSL_SET = 0x8276
OP_LIGHT_HSL_SET_UNACK = 0x8277
OP_LIGHT_HSL_STATUS = 0x8278

OP_LIGHT_XYL_GET = 0x8283
OP_LIGHT_XYL_SET = 0x8284
OP_LIGHT_XYL_SET_UNACK = 0x8285
OP_LIGHT_XYL_STATUS = 0x8286

MODEL_LIGHT_CTL_TEMPERATURE_SERVER = 0x1306
MODEL_LIGHT_HSL_SERVER = 0x1307
MODEL_LIGHT_XYL_SERVER = 0x130C


class LightHslStatus(NamedTuple):
    lightness: int
    hue: int
    saturation: int
    remaining_time: int | None


class LightXylStatus(NamedTuple):
    lightness: int
    x: int
    y: int
    remaining_time: int | None


def _u16(name: str, value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise AccessError(f"{name} out of uint16 range: {value}")
    return value.to_bytes(2, "little")


def _color_set(
    opcode: int,
    lightness: int,
    first: int,
    second: int,
    tid: int,
) -> bytes:
    if not 0 <= tid <= 0xFF:
        raise AccessError(f"TID out of range: {tid}")
    return (
        encode_opcode(opcode)
        + _u16("lightness", lightness)
        + _u16("first color component", first)
        + _u16("second color component", second)
        + bytes([tid])
    )


def _color_status(payload: bytes, opcode: int) -> tuple[int, int, int, int | None]:
    actual_opcode, params = parse_access(payload)
    if actual_opcode != opcode:
        raise AccessError(
            f"expected opcode {opcode:#06x}, got {actual_opcode:#06x}"
        )
    if len(params) not in (6, 7):
        raise AccessError(
            f"opcode {opcode:#06x} expects 6 or 7 parameter bytes, "
            f"got {len(params)}"
        )
    return (
        int.from_bytes(params[0:2], "little"),
        int.from_bytes(params[2:4], "little"),
        int.from_bytes(params[4:6], "little"),
        params[6] if len(params) == 7 else None,
    )


def config_network_transmit_set(count: int, interval_steps: int) -> bytes:
    """Build Config Network Transmit Set."""
    if not 0 <= count <= 7:
        raise AccessError("network transmit count must be 0..7")
    if not 0 <= interval_steps <= 31:
        raise AccessError("network transmit interval steps must be 0..31")
    return encode_opcode(OP_CONFIG_NETWORK_TRANSMIT_SET) + bytes(
        (count | (interval_steps << 3),)
    )


def light_hsl_get() -> bytes:
    return encode_opcode(OP_LIGHT_HSL_GET)


def light_hsl_set(
    lightness: int, hue: int, saturation: int, tid: int, *, ack: bool = True
) -> bytes:
    opcode = OP_LIGHT_HSL_SET if ack else OP_LIGHT_HSL_SET_UNACK
    return _color_set(opcode, lightness, hue, saturation, tid)


def parse_light_hsl_status(payload: bytes) -> LightHslStatus:
    return LightHslStatus(*_color_status(payload, OP_LIGHT_HSL_STATUS))


def light_xyl_get() -> bytes:
    return encode_opcode(OP_LIGHT_XYL_GET)


def light_xyl_set(
    lightness: int, x: int, y: int, tid: int, *, ack: bool = True
) -> bytes:
    opcode = OP_LIGHT_XYL_SET if ack else OP_LIGHT_XYL_SET_UNACK
    return _color_set(opcode, lightness, x, y, tid)


def parse_light_xyl_status(payload: bytes) -> LightXylStatus:
    return LightXylStatus(*_color_status(payload, OP_LIGHT_XYL_STATUS))
