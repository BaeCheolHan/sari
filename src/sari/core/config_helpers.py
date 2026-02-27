"""설정 파일/CSV 공통 헬퍼."""

from __future__ import annotations

import json
import logging
from pathlib import Path


def load_user_config() -> dict[str, object]:
    """사용자 설정 파일을 읽어 딕셔너리로 반환한다."""
    config_path = Path.home() / ".sari" / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        logging.getLogger(__name__).warning("사용자 설정 파일을 읽는 데 실패했습니다(path=%s): %s", config_path, exc)
        return {}
    except ValueError as exc:
        logging.getLogger(__name__).warning("사용자 설정 파일의 JSON이 잘못되었습니다(path=%s): %s", config_path, exc)
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def read_tuple_setting(file_config: dict[str, object], key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """설정 딕셔너리의 문자열 배열 값을 튜플로 파싱한다."""
    raw_value = file_config.get(key)
    if not isinstance(raw_value, list):
        return fallback
    parsed: list[str] = []
    for item in raw_value:
        if isinstance(item, str) and item.strip() != "":
            parsed.append(item.strip())
    if len(parsed) == 0:
        return fallback
    return tuple(parsed)


def parse_csv_setting(raw_value: str, default_value: tuple[str, ...]) -> tuple[str, ...]:
    """콤마 구분 환경변수를 튜플 설정으로 파싱한다."""
    if raw_value.strip() == "":
        return default_value
    parsed = [part.strip() for part in raw_value.split(",") if part.strip() != ""]
    if len(parsed) == 0:
        return default_value
    return tuple(parsed)
