"""EventBus 이벤트 타입 정의."""

from __future__ import annotations

from dataclasses import dataclass

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class L3FlushCompleted:
    """L3 flush 후 L4 데이터가 DB에 적재되었을 때 발행."""

    repo_root: str
    flushed_count: int  # flush된 파일 수


@dataclass(frozen=True)
class LspWarmReady:
    """LSP warm-up 완료 후 발행 (Wave2 probe 스케줄 완료 시점)."""

    repo_root: str
    language: Language
