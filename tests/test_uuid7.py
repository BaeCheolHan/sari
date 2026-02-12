import uuid

from sari.core.utils.uuid7 import uuid7, uuid7_hex


def test_uuid7_generator_returns_version7_uuid():
    u = uuid7()
    assert isinstance(u, uuid.UUID)
    assert u.version == 7


def test_uuid7_hex_returns_version7_hex():
    h = uuid7_hex()
    assert isinstance(h, str)
    assert len(h) == 32
    assert uuid.UUID(hex=h).version == 7
