#!/usr/bin/env python3
import json
import os
import tempfile
import time
from pathlib import Path

from sari.core.cjk import lindera_available, lindera_error
from sari.core.db import LocalSearchDB
from sari.core.engine_runtime import EmbeddedEngine
from sari.core.models import SearchOptions
from sari.core.workspace import WorkspaceManager


def _insert_doc(db: LocalSearchDB, path: str, repo: str, content: str) -> None:
    now = int(time.time())
    row = (
        path,
        repo,
        now,
        len(content.encode("utf-8")),
        content,
        now,
        "ok",
        "none",
        "ok",
        "none",
        0,
        0,
        0,
        len(content.encode("utf-8")),
    )
    db.upsert_files([row])


def main() -> int:
    print(f"[cjk] lindera_available={lindera_available()} err='{lindera_error()}'")
    with tempfile.TemporaryDirectory() as root_dir:
        root = Path(root_dir)
        db_path = root / ".codex" / "tools" / "sari" / "data" / "index.db"
        db = LocalSearchDB(str(db_path))
        root_id = WorkspaceManager.root_id(str(root))

        docs = [
            ("doc1.txt", "한글 형태소 분석 테스트 입니다"),
            ("doc2.txt", "검색 엔진 품질과 성능을 개선합니다"),
            ("doc3.txt", "데이터베이스 인덱싱 전략"),
            ("doc4.txt", "자연어 처리 파이프라인"),
            ("doc5.txt", "문서 요약 및 검색 결과"),
        ]
        for name, content in docs:
            path = f"{root_id}/{name}"
            _insert_doc(db, path, "__root__", content)

        cfg = type("Cfg", (), {"workspace_roots": [str(root)], "include_ext": [], "include_files": [], "exclude_dirs": [], "exclude_globs": [], "max_file_bytes": 0})
        engine = EmbeddedEngine(db, cfg, [str(root)])
        engine.rebuild()

        queries = [
            "형태소",
            "검색엔진",
            "데이터베이스",
            "자연어",
            "검색 결과",
        ]
        results = {}
        for q in queries:
            opts = SearchOptions(query=q, limit=5, offset=0, snippet_lines=3)
            hits, _meta = engine.search_v2(opts)
            results[q] = [h.path for h in hits]

        print(json.dumps(results, ensure_ascii=False, indent=2))
        ok = all(results[q] for q in queries)
        print(f"[cjk] ok={ok}")
        return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
