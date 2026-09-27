"""Tuya/Olight vendor-model datapoint access."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum

from btmesh.access import AccessError, encode_opcode, parse_access
from btmesh.node import MeshNode, ReceivedMessage


TUYA_COMPANY_ID = 0x07D0
TUYA_VENDOR_SERVER = 0x0004

OP_TUYA_DP_WRITE = 0xC9D007
OP_TUYA_DP_WRITE_UNACK = 0xCAD007
OP_TUYA_DP_READ = 0xCCD007
OP_TUYA_DP_DATA = 0xCDD007


class TuyaDpType(IntEnum):
    RAW = 0
    BOOL = 1
    VALUE = 2
    STRING = 3
    ENUM = 4
    BITMAP = 5


@dataclass(frozen=True)
class TuyaDpRecord:
    dp_id: int
    dp_type: TuyaDpType
    raw: bytes

    @property
    def value(self) -> bytes | bool | int | str:
        if self.dp_type is TuyaDpType.RAW:
            return self.raw
        if self.dp_type is TuyaDpType.BOOL:
            return self.raw != b"\x00"
        if self.dp_type in (
            TuyaDpType.VALUE,
            TuyaDpType.ENUM,
            TuyaDpType.BITMAP,
        ):
            return int.from_bytes(self.raw, "big")
        return self.raw.decode("utf-8")


@dataclass(frozen=True)
class TuyaDpData:
    command_type: int
    records: tuple[TuyaDpRecord, ...]


class VendorUnsupported(RuntimeError):
    pass


class VendorUnavailable(RuntimeError):
    pass


def _u8(name: str, value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise AccessError(f"{name} out of uint8 range: {value}")
    return value


def _variable(raw: bytes) -> bytes:
    if len(raw) > 0xFF:
        raise AccessError("datapoint value exceeds 255 bytes")
    return bytes([len(raw)]) + raw


def tuya_dp_record(
    dp_id: int,
    dp_type: TuyaDpType,
    value: bytes | bool | int | str,
) -> bytes:
    try:
        dp_type = TuyaDpType(dp_type)
    except ValueError as exc:
        raise AccessError(f"unsupported datapoint type: {dp_type!r}") from exc
    prefix = bytes([_u8("datapoint ID", dp_id), int(dp_type)])
    if dp_type is TuyaDpType.RAW:
        if not isinstance(value, bytes):
            raise AccessError("raw datapoint value must be bytes")
        return prefix + _variable(value)
    if dp_type is TuyaDpType.BOOL:
        if not isinstance(value, bool):
            raise AccessError("bool datapoint value must be bool")
        return prefix + bytes([value])
    if dp_type is TuyaDpType.VALUE:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 0xFFFFFFFF
        ):
            raise AccessError("value datapoint must be a uint32")
        return prefix + value.to_bytes(4, "big")
    if dp_type is TuyaDpType.STRING:
        if not isinstance(value, str):
            raise AccessError("string datapoint value must be str")
        return prefix + _variable(value.encode("utf-8"))
    if dp_type is TuyaDpType.ENUM:
        if not isinstance(value, int) or isinstance(value, bool):
            raise AccessError("enum datapoint value must be int")
        return prefix + bytes([_u8("enum datapoint value", value)])
    if dp_type is TuyaDpType.BITMAP:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 0xFFFFFFFF
        ):
            raise AccessError("bitmap datapoint value must be a uint32")
        length = 1 if value <= 0xFF else 2 if value <= 0xFFFF else 4
        return prefix + _variable(value.to_bytes(length, "big"))
    raise AccessError(f"unsupported datapoint type: {dp_type!r}")


def tuya_dp_read(dp_ids: list[int] | tuple[int, ...], command_type: int = 1) -> bytes:
    if not 1 <= len(dp_ids) <= 0xFF:
        raise AccessError("vendor read requires 1..255 datapoint IDs")
    params = bytes(
        [_u8("command type", command_type), len(dp_ids)]
        + [_u8("datapoint ID", dp_id) for dp_id in dp_ids]
    )
    return encode_opcode(OP_TUYA_DP_READ) + params


def tuya_dp_write(
    records: list[bytes] | tuple[bytes, ...],
    *,
    command_type: int = 1,
    ack: bool = True,
) -> bytes:
    if not records:
        raise AccessError("vendor write requires at least one datapoint record")
    opcode = OP_TUYA_DP_WRITE if ack else OP_TUYA_DP_WRITE_UNACK
    return (
        encode_opcode(opcode)
        + bytes([_u8("command type", command_type)])
        + b"".join(records)
    )


def parse_tuya_dp_data(payload: bytes) -> TuyaDpData:
    opcode, params = parse_access(payload)
    if opcode != OP_TUYA_DP_DATA:
        raise AccessError(
            f"expected opcode {OP_TUYA_DP_DATA:#08x}, got {opcode:#08x}"
        )
    if not params:
        raise AccessError("vendor DATA has no command type")
    command_type = params[0]
    if command_type not in (1, 5):
        raise AccessError(
            f"unsupported vendor DATA command type {command_type:#04x}"
        )
    records = []
    offset = 1
    while offset < len(params):
        if len(params) - offset < 2:
            raise AccessError("truncated vendor datapoint header")
        dp_id = params[offset]
        try:
            dp_type = TuyaDpType(params[offset + 1])
        except ValueError as exc:
            raise AccessError(
                f"unknown vendor datapoint type {params[offset + 1]:#04x}"
            ) from exc
        offset += 2
        fixed_length = {
            TuyaDpType.BOOL: 1,
            TuyaDpType.VALUE: 4,
            TuyaDpType.ENUM: 1,
        }.get(dp_type)
        if fixed_length is None:
            if offset >= len(params):
                raise AccessError("missing vendor datapoint length")
            value_length = params[offset]
            offset += 1
            if dp_type is TuyaDpType.BITMAP and value_length not in (1, 2, 4):
                raise AccessError("bitmap datapoint length must be 1, 2, or 4")
        else:
            value_length = fixed_length
        end = offset + value_length
        if end > len(params):
            raise AccessError("truncated vendor datapoint value")
        record = TuyaDpRecord(dp_id, dp_type, params[offset:end])
        try:
            record.value
        except UnicodeDecodeError as exc:
            raise AccessError("string datapoint is not valid UTF-8") from exc
        records.append(record)
        offset = end
    return TuyaDpData(command_type, tuple(records))


def vendor_model_element(composition: dict) -> int:
    for element in composition.get("elements", []):
        for model in element.get("vendor_models", []):
            if (
                model.get("company_id") == TUYA_COMPANY_ID
                and model.get("model_id") == TUYA_VENDOR_SERVER
            ):
                return element["address"]
    raise VendorUnsupported(
        "saved composition has no Tuya/Olight vendor server 0x07d0:0x0004"
    )


def _payload(message: ReceivedMessage) -> bytes:
    return encode_opcode(message.opcode) + message.params


class TuyaVendorController:
    def __init__(
        self,
        node: MeshNode,
        composition: dict,
        *,
        timeout: float = 5.0,
        response_settle: float = 0.1,
    ) -> None:
        self._node = node
        self._composition = composition
        self._timeout = timeout
        self._response_settle = response_settle

    async def read(self, dp_ids: list[int] | tuple[int, ...]) -> TuyaDpData:
        address = vendor_model_element(self._composition)
        received = self._node.received
        start = len(received)
        try:
            response = await self._node.request(
                address,
                tuya_dp_read(dp_ids),
                OP_TUYA_DP_DATA,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise VendorUnavailable(
                f"Sphere element {address:#06x} did not answer vendor DP Read"
            ) from exc
        await asyncio.sleep(self._response_settle)
        messages = [
            message
            for message in list(received)[start:]
            if message.src == address and message.opcode == OP_TUYA_DP_DATA
        ]
        if response not in messages:
            messages.insert(0, response)
        parsed = [parse_tuya_dp_data(_payload(message)) for message in messages]
        command_type = parsed[-1].command_type
        if any(data.command_type != command_type for data in parsed):
            raise AccessError("vendor DATA burst has inconsistent command types")
        records = tuple(record for data in parsed for record in data.records)
        return TuyaDpData(command_type, records)


def json_value(record: TuyaDpRecord) -> bool | int | str:
    value = record.value
    return value.hex() if isinstance(value, bytes) else value
