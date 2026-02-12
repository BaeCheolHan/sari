import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    native = getattr(uuid, "uuid7", None)
    if callable(native):
        return native()

    # RFC 9562 UUIDv7 layout:
    # time_ms(48) | version(4) | rand_a(12) | variant(2) | rand_b(62)
    time_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = (
        (time_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)


def uuid7_hex() -> str:
    return uuid7().hex
