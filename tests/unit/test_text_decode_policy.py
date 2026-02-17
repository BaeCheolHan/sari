"""텍스트 디코딩 정책 유틸을 검증한다."""

from sari.core.text_decode import decode_bytes_with_policy


def test_decode_bytes_with_policy_supports_cp949() -> None:
    """cp949 바이트는 fallback 체인으로 정상 디코딩되어야 한다."""
    raw = "한글 테스트".encode("cp949")

    decoded = decode_bytes_with_policy(raw)

    assert decoded.text == "한글 테스트"
    assert decoded.encoding_used in {"cp949", "euc-kr"}


def test_decode_bytes_with_policy_marks_warning_on_surrogateescape() -> None:
    """유효하지 않은 utf-8 바이트는 warning과 함께 복원되어야 한다."""
    raw = b"\xff\xfe\xfd"

    decoded = decode_bytes_with_policy(raw)

    assert decoded.decode_warning is not None
    assert decoded.text != ""
