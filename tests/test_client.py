import asyncio

import pytest

from spherosphere import client
from spherosphere.client import parse_args


def test_vendor_read_accepts_decimal_and_prefixed_ids():
    args = parse_args(["vendor-read", "2", "0x66"])

    assert args.command == "vendor-read"
    assert args.dp_ids == [2, 102]


def test_effect_cli_parses_named_operations():
    for operation in ("set", "select", "activate"):
        args = parse_args(["effect", operation, "aurora"])
        assert (args.command, args.operation, args.name) == (
            "effect", operation, "aurora"
        )
    args = parse_args(["effect", "get"])
    assert (args.command, args.operation) == ("effect", "get")


def test_countdown_cli_parses_named_operations():
    for operation in ("get", "cancel"):
        args = parse_args(["countdown", operation])
        assert (args.command, args.operation) == ("countdown", operation)
    args = parse_args(["countdown", "set", "43200"])
    assert (args.command, args.operation, args.seconds) == (
        "countdown", "set", 43200,
    )


@pytest.mark.parametrize(
    ("argv", "method", "params"),
    [
        (["devices"], "device.list", {}),
        (["rename", "0x012a", "ARI"], "device.rename", {"device": "0x012a", "name": "ARI"}),
        (["enroll"], "enrollment.start", {"duration": 120}),
        (["enroll", "--seconds", "30"], "enrollment.start", {"duration": 30}),
        (["enrollment"], "enrollment.status", {}),
        (["enrollment", "status"], "enrollment.status", {}),
        (["enrollment", "stop"], "enrollment.stop", {}),
        (["--device", "ARI", "status"], "status", {"device": "ARI"}),
        (["--device", "ARI", "control", "on"], "control", {"command": "on", "device": "ARI"}),
        (["--device", "ARI", "lighting", "read"], "lighting", {"command": "read", "device": "ARI"}),
        (["--device", "ARI", "vendor-read", "0x66"], "vendor.read", {"dp_ids": [102], "device": "ARI"}),
        (["--device", "ARI", "effect", "list"], "effect.list", {}),
        (["--device", "ARI", "effect", "get"], "effect.get", {"device": "ARI"}),
        *[(["--device", "ARI", "effect", op, "aurora"], f"effect.{op}", {"name": "aurora", "device": "ARI"}) for op in ("set", "select", "activate")],
        *[(["--device", "ARI", "countdown", op], f"countdown.{op}", {"device": "ARI"}) for op in ("get", "cancel")],
        (["--device", "ARI", "countdown", "set", "30"], "countdown.set", {"seconds": 30, "device": "ARI"}),
        (["control", "on"], "control", {"command": "on"}),
    ],
)
def test_cli_dispatch(monkeypatch, argv, method, params, capsys):
    calls = []

    async def fake_request(socket, actual_method, actual_params):
        calls.append((actual_method, actual_params))
        return {"ok": True, "result": {}}

    monkeypatch.setattr(client, "request", fake_request)
    assert asyncio.run(client.async_main(parse_args(argv))) == 0
    assert calls == [(method, params)]
    assert capsys.readouterr().out.strip() == "{}"
