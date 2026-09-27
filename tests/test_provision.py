import json
import stat
from types import SimpleNamespace

import pytest

from sphere_mesh import provision as sphere
from btmesh.bearer import PROXY_DATA_IN, PROXY_DATA_OUT, PROXY_SERVICE
from sphere_mesh.transport import GattBearer


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
    ],
)
def test_run_modes_are_mutually_exclusive(argv):
    with pytest.raises(SystemExit):
        sphere.parse_args(argv)


def test_local_transport_is_default():
    args = sphere.parse_args(["--init-only"])
    assert args.transport == "local"


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
