"""solidlsp에서 사용하는 문자열 유틸을 제공한다."""


class ToStringMixin:
    """객체 문자열 표현을 단순 제공한다."""

    def _tostring_includes(self) -> list[str]:
        """문자열 표현에 포함할 속성 목록을 반환한다."""
        return []

    def __str__(self) -> str:
        """속성 기반 문자열 표현을 생성한다."""
        keys = self._tostring_includes()
        pairs: list[str] = []
        for key in keys:
            pairs.append(f"{key}={getattr(self, key, None)!r}")
        return f"{self.__class__.__name__}({', '.join(pairs)})"
