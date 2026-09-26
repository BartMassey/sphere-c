"""Sphere-required Mesh configuration messages absent from btmesh 0.10.3."""

from btmesh.access import AccessError, encode_opcode

OP_CONFIG_NETWORK_TRANSMIT_SET = 0x8024
OP_CONFIG_NETWORK_TRANSMIT_STATUS = 0x8025


def config_network_transmit_set(count: int, interval_steps: int) -> bytes:
    """Build Config Network Transmit Set."""
    if not 0 <= count <= 7:
        raise AccessError("network transmit count must be 0..7")
    if not 0 <= interval_steps <= 31:
        raise AccessError("network transmit interval steps must be 0..31")
    return encode_opcode(OP_CONFIG_NETWORK_TRANSMIT_SET) + bytes(
        (count | (interval_steps << 3),)
    )
