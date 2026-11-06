import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from spherosphere import provision as sphere
from btmesh.bearer import PROXY_DATA_IN, PROXY_DATA_OUT, PROXY_SERVICE
from spherosphere.transport import GattBearer


def test_state_is_created_with_private_permissions(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = sphere.load_or_create_state(path)

    assert state["next_unicast"] == 0x0100
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(bytes.fromhex(state["netkey"])) == 16
    assert len(bytes.fromhex(state["appkey"])) == 16


def test_save_state_is_atomic(tmp_path):
    path = tmp_path / "state.json"
    state = sphere.load_or_create_state(path)
    state["seq"] = 42
    sphere.save_state(path, state)

    assert json.loads(path.read_text())["seq"] == 42
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--init-only", "--provision-only"],
        ["--init-only", "--toggle-only"],
        ["--provision-only", "--toggle-only"],
        ["--provision-only", "--reset-node"],
        ["--toggle-only", "--reset-node"],
        ["--toggle-only", "--control", "read"],
        ["--reset-node", "--control", "read"],
        ["--init-only", "--control", "read"],
        ["--control", "read", "--lighting", "lightness"],
        ["--toggle-only", "--lighting", "lightness"],
        ["--reset-node", "--lighting", "lightness"],
        ["--init-only", "--lighting", "lightness"],
    ],
)
def test_run_modes_are_mutually_exclusive(argv):
    with pytest.raises(SystemExit):
        sphere.parse_args(argv)


def test_local_transport_is_default():
    args = sphere.parse_args(["--init-only"])
    assert args.transport == "local"


@pytest.mark.parametrize("other", [
    ["--init-only"], ["--provision-only"], ["--toggle-only"],
    ["--reset-node"], ["--control", "read"], ["--lighting", "lightness"],
])
def test_composition_probe_excludes_other_run_modes(other):
    with pytest.raises(SystemExit):
        sphere.parse_args(["--probe-composition", *other])


async def test_composition_probe_uses_saved_node_without_configuration(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "state.json"
    state = sphere.load_or_create_state(path)
    state["devices"]["0x0100"] = {
        "uuid": "00" * 16, "device_key": "11" * 16,
    }
    sphere.save_state(path, state)
    transport = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(sphere, "LocalTransport", lambda: transport)
    scanner = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(sphere, "_find_proxy_target", AsyncMock(
        return_value=(SimpleNamespace(address="test-device"), scanner),
    ))
    reply = SimpleNamespace(
        opcode=sphere.OP_CONFIG_COMPOSITION_DATA_STATUS,
        params=bytes.fromhex("00 d007 0100 0100 0800 0000 0000 01 00 0010"),
    )
    node = SimpleNamespace(request=AsyncMock(return_value=reply))
    session = SimpleNamespace(
        node=node, pump=SimpleNamespace(failure=None), close=AsyncMock(),
    )
    open_session = AsyncMock(return_value=session)
    monkeypatch.setattr(sphere, "open_mesh_proxy_session", open_session)
    forbidden = AsyncMock(side_effect=AssertionError("unexpected device mutation"))
    for name in ("provision", "configure", "toggle_loop", "read_control_state"):
        monkeypatch.setattr(sphere, name, forbidden)
    args = sphere.parse_args(["--state", str(path), "--probe-composition"])

    assert await sphere.async_main(args) == 0

    node.request.assert_awaited_once_with(
        0x0100, sphere.config_composition_data_get(0),
        sphere.OP_CONFIG_COMPOSITION_DATA_STATUS,
        dev_key=True, timeout=15, retries=1,
    )
    forbidden.assert_not_awaited()
    session.close.assert_awaited_once()
    scanner.stop.assert_awaited_once()
    transport.stop.assert_awaited_once()
    assert "authenticated composition:" in capsys.readouterr().out
    assert "control_model" not in json.loads(path.read_text())


async def test_composition_probe_reports_timeout_as_diagnostic_failure():
    node = SimpleNamespace(request=AsyncMock(side_effect=TimeoutError("no reply")))
    with pytest.raises(sphere.Phase0Failure) as failure:
        await sphere.inspect_composition(node, 0x0100, SimpleNamespace(failure=None))
    assert failure.value.phase == "inspect Composition Data"


def test_control_commands_can_be_batched():
    args = sphere.parse_args(
        ["--control", "read", "--control", "on", "--control", "off"]
    )

    assert args.control == ["read", "on", "off"]


def test_lighting_commands_can_be_batched():
    args = sphere.parse_args(
        ["--lighting", "lightness", "--lighting", "hsl=1,2,3"]
    )

    assert args.lighting[0].model == "lightness"
    assert args.lighting[0].values is None
    assert args.lighting[1].model == "hsl"
    assert args.lighting[1].values == (1, 2, 3)


def test_pick_device_prefers_active_address_for_duplicate_uuid():
    uuid = "00112233445566778899aabbccddeeff"
    state = {
        "active_device": "0x0118",
        "devices": {
            "0x0112": {"uuid": uuid},
            "0x0118": {"uuid": uuid},
        },
    }

    selected = sphere.pick_device(state, SimpleNamespace(device_uuid=uuid))

    assert selected == 0x0118


def test_gatt_bearer_selects_proxy_characteristics_by_handle():
    class Characteristic:
        def __init__(self, uuid, handle):
            self.uuid = uuid
            self.handle = handle

    class Service:
        def __init__(self, uuid, characteristics):
            self.uuid = uuid
            self.characteristics = characteristics

    expected_in = Characteristic(PROXY_DATA_IN, 0x31)
    expected_out = Characteristic(PROXY_DATA_OUT, 0x34)
    duplicate_out = Characteristic(PROXY_DATA_OUT, 0x38)
    client = type(
        "Client",
        (),
        {
            "services": [
                Service("00001827-0000-1000-8000-00805f9b34fb", [duplicate_out]),
                Service(PROXY_SERVICE, [expected_in, expected_out]),
            ]
        },
    )()

    bearer = GattBearer(client)
    assert bearer._data_in is expected_in
    assert bearer._data_out is expected_out
