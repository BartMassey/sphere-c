from sphere_mesh.client import parse_args


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
