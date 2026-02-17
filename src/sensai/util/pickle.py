"""solidlsp에서 사용하는 pickle 유틸을 제공한다."""

from __future__ import annotations

import pickle
from pathlib import Path


def getstate(_cls: type[object], instance: object, transient_properties: list[str] | None = None) -> dict[str, object]:
    """객체 상태에서 일시 필드를 제외한 딕셔너리를 반환한다."""
    state = dict(instance.__dict__)
    for key in transient_properties or []:
        state.pop(key, None)
    return state


def dump_pickle(obj: object, path: str) -> None:
    """객체를 pickle 파일로 저장한다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fp:
        pickle.dump(obj, fp)


def load_pickle(path: str, default: object | None = None) -> object | None:
    """pickle 파일을 읽고 실패하면 기본값을 반환한다."""
    target = Path(path)
    if not target.exists():
        return default
    with target.open("rb") as fp:
        return pickle.load(fp)
