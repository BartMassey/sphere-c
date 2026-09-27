from sphere_mesh.client import parse_args


def test_vendor_read_accepts_decimal_and_prefixed_ids():
    args = parse_args(["vendor-read", "2", "0x66"])

    assert args.command == "vendor-read"
    assert args.dp_ids == [2, 102]
