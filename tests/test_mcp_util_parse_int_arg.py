from sari.mcp.tools._util import parse_int_arg


def test_parse_int_arg_accepts_float_like_integer_string():
    value, err = parse_int_arg({"limit": "10.0"}, "limit", 5, "tool", min_value=1)
    assert err is None
    assert value == 10


def test_parse_int_arg_rejects_fractional_string():
    value, err = parse_int_arg({"limit": "10.5"}, "limit", 5, "tool", min_value=1)
    assert value is None
    assert err is not None
