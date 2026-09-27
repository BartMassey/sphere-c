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
    countdown_dp_write,
    effect_catalog,
    effect_selection,
    effect_value,
    guarded_dp_write,
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
    assert guarded_dp_write(effect_selection("colour_2")) == bytes.fromhex(
        "c9d00701020401660402"
    )


def test_countdown_write_uses_only_dp_101_value():
    assert countdown_dp_write(43200) == bytes.fromhex("c9d0070165020000a8c0")
    assert countdown_dp_write(0) == bytes.fromhex("c9d00701650200000000")


@pytest.mark.parametrize("seconds", [-1, 43201, True, 1.0, "10", None])
def test_countdown_write_rejects_invalid_seconds(seconds):
    with pytest.raises(AccessError):
        countdown_dp_write(seconds)


def test_effect_catalog_preserves_schema_and_visible_picker():
    catalog = effect_catalog()
    assert len(catalog) == 32
    assert catalog[0] == {
        "name": "flame", "canonical": "flame", "slug": "flame",
        "value": 0, "label": "Flame", "visible": True,
    }
    assert catalog[7]["label"] == "Color Wheel"
    assert catalog[31]["name"] == "colour_31"
    assert {item["value"] for item in catalog if not item["visible"]} == {
        5, 14, 19, 22, 23, 27,
    }
    visible = [item for item in catalog if item["visible"]]
    assert len(visible) == 26
    assert len({item["slug"] for item in visible}) == 26
    assert all(item["slug"] is None for item in catalog if not item["visible"])
    for item in visible:
        assert effect_value(item["slug"], visible_only=True) == item["value"]
        assert effect_value(item["canonical"], visible_only=True) == item["value"]
    assert catalog[23]["label"] == catalog[29]["label"] == "Christmas"
    assert catalog[27]["label"] == catalog[31]["label"] == "Snowfall"
    assert effect_value("christmas", visible_only=True) == 29
    assert effect_value("snowfall", visible_only=True) == 31


@pytest.mark.parametrize("name,value", [
    ("aurora", 2), ("color-wheel", 7), ("cotton-candy", 1),
    ("candy-cane", 30), ("flame", 0),
])
def test_effect_selection_accepts_visible_slug(name, value):
    assert effect_selection(name) == {2: 1, 102: value}


@pytest.mark.parametrize("name", [
    "colour_5", "colour_22", "colour_0", "colour_32", "colour_02",
    "red", "firefly", "floating", "autumn", "new-year", 2,
])
def test_effect_selection_rejects_non_visible_or_invalid_name(name):
    with pytest.raises(ValueError):
        effect_selection(name)


@pytest.mark.parametrize("values", [
    {}, {1: 1}, {2: 4}, {102: 32}, {2: True},
    {102: "2"}, {2: 1, 8: 0},
    *({102: value} for value in (5, 14, 19, 22, 23, 27)),
])
def test_guarded_write_rejects_unknown_or_invalid_datapoints(values):
    with pytest.raises(AccessError):
        guarded_dp_write(values)


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


async def test_vendor_controller_write_accepts_empty_ack_and_snapshot_burst():
    node = Mock()
    node.received = deque()
    empty = ReceivedMessage(src=0x0118, opcode=OP_TUYA_DP_DATA, params=b"\x01")
    snapshot = ReceivedMessage(
        src=0x0118, opcode=OP_TUYA_DP_DATA,
        params=bytes.fromhex("01660402"),
    )

    async def request(*args, **kwargs):
        node.received.extend((empty, snapshot))
        return empty

    node.request = AsyncMock(side_effect=request)
    controller = TuyaVendorController(node, COMPOSITION, response_settle=0)
    result = await controller.write(effect_selection("colour_2"))

    assert [(record.dp_id, record.value) for record in result.records] == [(102, 2)]
    node.request.assert_awaited_once_with(
        0x0118, bytes.fromhex("c9d00701020401660402"),
        OP_TUYA_DP_DATA, timeout=5.0, retries=0,
    )


async def test_vendor_controller_write_accepts_empty_ack_only():
    node = Mock()
    ack = ReceivedMessage(src=0x0118, opcode=OP_TUYA_DP_DATA, params=b"\x01")
    node.received = deque([ack])
    node.request = AsyncMock(return_value=ack)
    controller = TuyaVendorController(node, COMPOSITION, response_settle=0)
    result = await controller.write({2: 1})
    assert result.records == ()


async def test_vendor_controller_collects_burst_when_receive_history_is_full():
    node = Mock()
    node.received = deque(
        [ReceivedMessage(src=0x0118, opcode=OP_TUYA_DP_DATA, params=b"\x01")],
        maxlen=1,
    )
    response = ReceivedMessage(
        src=0x0118, opcode=OP_TUYA_DP_DATA, params=bytes.fromhex("01660407")
    )

    async def request(*args, **kwargs):
        node.received.append(response)
        return response

    node.request = AsyncMock(side_effect=request)
    controller = TuyaVendorController(node, COMPOSITION, response_settle=0)
    result = await controller.read([102])
    assert [(record.dp_id, record.value) for record in result.records] == [(102, 7)]


async def test_vendor_controller_write_reports_timeout():
    node = Mock()
    node.received = deque()
    node.request = AsyncMock(side_effect=TimeoutError)
    controller = TuyaVendorController(node, COMPOSITION)
    with pytest.raises(VendorUnavailable, match="DP Write"):
        await controller.write({102: effect_value("colour_2")})
