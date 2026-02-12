from pathlib import Path
from sari.core.utils.file import _parse_size, _is_minified, _printable_ratio, _sample_file

def test_parse_size():
    assert _parse_size(None, 100) == 100
    assert _parse_size("", 100) == 100
    assert _parse_size("  ", 100) == 100
    assert _parse_size("1024", 100) == 1024
    assert _parse_size("1KB", 100) == 1024
    assert _parse_size("1mb", 100) == 1024 * 1024
    assert _parse_size("1GB", 100) == 1024 * 1024 * 1024
    assert _parse_size("1TB", 100) == 1024 * 1024 * 1024 * 1024
    assert _parse_size("1,024KB", 100) == 1024 * 1024
    assert _parse_size("0.5MB", 100) == 512 * 1024
    assert _parse_size("invalid", 100) == 100

def test_is_minified():

    assert _is_minified(Path("test.min.js"), "some text") is True

    assert _is_minified(Path("test.js"), "") is False

    assert _is_minified(Path("test.js"), "a" * 301) is True

    assert _is_minified(Path("test.js"), "line1\nline2") is False

    assert _is_minified(Path("test.js"), "a" * 301 + "\n" + "b" * 301) is True



def test_printable_ratio():

    assert _printable_ratio(b"") == 1.0

    assert _printable_ratio(b"\x00\x01\x02") == 0.0

    assert _printable_ratio(b"Hello World") == 1.0

    assert _printable_ratio(b"Hello\nWorld\t") == 1.0

    # UTF-8 decode error

    assert _printable_ratio(b"\xff\xfe\xfd") == 0.0

    # Non-printable characters (e.g. control chars other than \t, \n, \r)

    assert _printable_ratio(b"Hello\x07World", policy="ignore") < 1.0

def test_sample_file(tmp_path):
    p = tmp_path / "test.txt"
    content = b"A" * 10000 + b"B" * 10000
    p.write_bytes(content)
    
    sample = _sample_file(p, len(content))
    # _TEXT_SAMPLE_BYTES = 8192
    assert len(sample) == 8192 * 2
    assert sample.startswith(b"A" * 8192)
    assert sample.endswith(b"B" * 8192)

    # Small file
    p2 = tmp_path / "small.txt"
    p2.write_bytes(b"hello")
    sample2 = _sample_file(p2, 5)
    assert sample2 == b"hello"

    # Non-existent file
    sample3 = _sample_file(tmp_path / "none.txt", 10)
    assert sample3 == b""
