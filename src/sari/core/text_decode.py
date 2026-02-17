"""텍스트 디코딩 정책 유틸을 제공한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecodedTextDTO:
    """디코딩 결과와 메타 정보를 표현한다."""

    text: str
    encoding_used: str
    decode_warning: str | None


def decode_bytes_with_policy(raw_bytes: bytes) -> DecodedTextDTO:
    """다중 인코딩 체인으로 바이트를 텍스트로 복원한다."""
    attempts = ("utf-8", "utf-8-sig", "cp949", "euc-kr")
    for encoding_name in attempts:
        try:
            return DecodedTextDTO(text=raw_bytes.decode(encoding_name, errors="strict"), encoding_used=encoding_name, decode_warning=None)
        except UnicodeDecodeError:
            continue
    # 마지막 단계는 surrogateescape로 복원하고 warning을 남긴다.
    recovered = raw_bytes.decode("utf-8", errors="surrogateescape")
    return DecodedTextDTO(
        text=recovered,
        encoding_used="utf-8-surrogateescape",
        decode_warning="fallback decode used: surrogateescape",
    )
