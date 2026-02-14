from sari.mcp.cli import utils


class _BadArgs:
    def __getattr__(self, _name):
        raise RuntimeError("boom")


def test_get_arg_returns_default_when_getattr_raises():
    assert utils.get_arg(_BadArgs(), "x", "d") == "d"


def test_is_port_in_use_returns_true_for_invalid_port_value():
    assert utils.is_port_in_use("127.0.0.1", "bad") is True
    assert utils.is_port_in_use("127.0.0.1", 70000) is True
