"""설정 문자열 파서 유틸리티."""

from __future__ import annotations


class ConfigValueParser:
    """문자열 환경변수 값을 숫자/불리언 설정으로 안전하게 변환한다."""

    _TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
    _FALSE_VALUES = frozenset({"0", "false", "no", "off"})

    def int_min(self, raw: str, *, minimum: int, default: int) -> int:
        try:
            return max(minimum, int(raw))
        except ValueError:
            return default

    def int_range(self, raw: str, *, minimum: int, maximum: int, default: int) -> int:
        try:
            value = int(raw)
        except ValueError:
            return default
        return min(maximum, max(minimum, value))

    def float_min(self, raw: str, *, minimum: float, default: float) -> float:
        try:
            return max(minimum, float(raw))
        except ValueError:
            return default

    def float_range(self, raw: str, *, minimum: float, maximum: float, default: float) -> float:
        try:
            value = float(raw)
        except ValueError:
            return default
        return min(maximum, max(minimum, value))

    def parse_lane_bundle(
        self,
        *,
        hot_raw: str,
        backlog_raw: str,
        sticky_raw: str,
        switch_raw: str,
        min_lease_raw: str,
        default: tuple[int, int, float, float, int],
    ) -> tuple[int, int, float, float, int]:
        """lane 5개 값을 묶어서 파싱한다.

        기존 동작 호환:
        - 묶음 내 하나라도 파싱 실패하면 전체를 default로 되돌린다.
        """
        try:
            hot = max(0, int(hot_raw))
            backlog = max(0, int(backlog_raw))
            sticky = max(0.0, float(sticky_raw))
            switch = max(0.0, float(switch_raw))
            min_lease = max(0, int(min_lease_raw))
        except ValueError:
            return default
        return (hot, backlog, sticky, switch, min_lease)

    def bool_true(self, raw: str) -> bool:
        return raw in self._TRUE_VALUES

    def bool_enabled(self, raw: str) -> bool:
        return raw not in self._FALSE_VALUES
