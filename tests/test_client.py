from sphere_mesh.client import parse_args


def test_vendor_read_accepts_decimal_and_prefixed_ids():
    args = parse_args(["vendor-read", "2", "0x66"])

    assert args.command == "vendor-read"
    assert args.dp_ids == [2, 102]


def test_effect_cli_parses_named_operations():
    for operation in ("set", "select", "activate"):
        args = parse_args(["effect", operation, "colour_2"])
        assert (args.command, args.operation, args.name) == (
            "effect", operation, "colour_2"
        )
    args = parse_args(["effect", "get"])
    assert (args.command, args.operation) == ("effect", "get")
