from collections import deque
from unittest.mock import AsyncMock, Mock

import pytest

from btmesh.access import AccessError
from btmesh.node import ReceivedMessage
from sphere_mesh.vendor import (
    OP_TUYA_DP_DATA,
    TuyaDpType,
    TuyaVendorController,
    VendorUnavailable,
    VendorUnsupported,
    parse_tuya_dp_data,
    tuya_dp_read,
    tuya_dp_record,
    tuya_dp_write,
    vendor_model_element,
)


COMPOSITION = {
    "elements": [
        {
            "address": 0x0118,
            "vendor_models": [
                {"company_id": 0x07D0, "model_id": 0x0004},
                {"company_id": 0x07D0, "model_id": 0x0005},
            ],
        }
    ]
}


def test_vendor_read_matches_apk_layout():
    assert tuya_dp_read([2, 102]) == bytes.fromhex("ccd00701020266")


def test_vendor_write_matches_derived_effect_vector():
    records = [
        tuya_dp_record(2, TuyaDpType.ENUM, 1),
        tuya_dp_record(102, TuyaDpType.ENUM, 2),
    ]
    assert tuya_dp_write(records) == bytes.fromhex(
        "c9d00701020401660402"
    )
    assert tuya_dp_write(records, ack=False) == bytes.fromhex(
        "cad00701020401660402"
    )


def test_parse_vendor_data_typed_records():
    data = parse_tuya_dp_data(
        bytes.fromhex(
            "cdd00701"
            "020401"
            "66041e"
            "010101"
            "2102000003e8"
            "080003aabbcc"
            "090303666f6f"
            "0a05021234"
        )
    )

    assert data.command_type == 1
    assert [(record.dp_id, record.dp_type, record.value) for record in data.records] == [
        (2, TuyaDpType.ENUM, 1),
        (102, TuyaDpType.ENUM, 30),
        (1, TuyaDpType.BOOL, True),
        (33, TuyaDpType.VALUE, 1000),
        (8, TuyaDpType.RAW, bytes.fromhex("aabbcc")),
        (9, TuyaDpType.STRING, "foo"),
        (10, TuyaDpType.BITMAP, 0x1234),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        bytes.fromhex("c9d00701020401"),
        bytes.fromhex("cdd007"),
        bytes.fromhex("cdd0070102"),
        bytes.fromhex("cdd007010206"),
        bytes.fromhex("cdd007010200"),
        bytes.fromhex("cdd00701020503ffffff"),
        bytes.fromhex("cdd00702020401"),
    ],
)
def test_vendor_data_rejects_wrong_or_malformed_payload(payload):
    with pytest.raises(AccessError):
        parse_tuya_dp_data(payload)


def test_vendor_model_element_uses_authenticated_composition():
    assert vendor_model_element(COMPOSITION) == 0x0118
    with pytest.raises(VendorUnsupported):
        vendor_model_element({"elements": []})


async def test_vendor_controller_reads_and_parses_response():
    node = Mock()
    node.received = deque()
    first = ReceivedMessage(
        src=0x0118,
        opcode=OP_TUYA_DP_DATA,
        params=bytes.fromhex("01"),
    )
    second = ReceivedMessage(
        src=0x0118,
        opcode=OP_TUYA_DP_DATA,
        params=bytes.fromhex("01020401660407"),
    )

    async def request(*args, **kwargs):
        node.received.extend((first, second))
        return first

    node.request = AsyncMock(side_effect=request)
    controller = TuyaVendorController(node, COMPOSITION, response_settle=0)

    result = await controller.read([2, 102])

    assert [record.value for record in result.records] == [1, 7]
    node.request.assert_awaited_once_with(
        0x0118,
        tuya_dp_read([2, 102]),
        OP_TUYA_DP_DATA,
        timeout=5.0,
    )


async def test_vendor_controller_reports_timeout():
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(side_effect=TimeoutError)
    controller = TuyaVendorController(node, COMPOSITION)

    with pytest.raises(VendorUnavailable):
        await controller.read([2, 102])
