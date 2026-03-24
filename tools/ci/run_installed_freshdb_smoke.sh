#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${ROOT_DIR}/artifacts/ci"
SMOKE_DB_PATH="${ARTIFACT_DIR}/installed-freshdb-smoke.db"
SMOKE_SUMMARY_PATH="${ARTIFACT_DIR}/installed-freshdb-smoke-summary.json"

mkdir -p "${ARTIFACT_DIR}"
rm -f "${SMOKE_DB_PATH}" "${SMOKE_DB_PATH}-wal" "${SMOKE_DB_PATH}-shm" "${SMOKE_SUMMARY_PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[installed-freshdb-smoke] uv command not found." >&2
  exit 1
fi

# Always validate the actual installed CLI runtime, not local source imports.
uv tool install --reinstall "${ROOT_DIR}" >/dev/null

SARI_BIN="$(command -v sari || true)"
if [[ -z "${SARI_BIN}" ]]; then
  echo "[installed-freshdb-smoke] sari command not found after uv tool install." >&2
  exit 1
fi
SARI_PY="$(head -n 1 "${SARI_BIN}" | sed 's/^#!//')"
if [[ -z "${SARI_PY}" || ! -x "${SARI_PY}" ]]; then
  echo "[installed-freshdb-smoke] unable to resolve installed sari interpreter." >&2
  exit 1
fi

export SARI_DB_PATH="${SMOKE_DB_PATH}"
export SARI_SMOKE_ROOT="${ROOT_DIR}"
export SARI_SMOKE_SUMMARY="${SMOKE_SUMMARY_PATH}"

"${SARI_PY}" - <<'PY'
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import sari
from sari.core.composition import (
    build_file_collection_service_from_config,
    build_repository_bundle,
)
from sari.core.config import AppConfig
from sari.core.models import LspExtractPersistDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.schema import init_schema
from sari.services.workspace import WorkspaceService

root_dir = Path(os.environ["SARI_SMOKE_ROOT"]).resolve()
summary_path = Path(os.environ["SARI_SMOKE_SUMMARY"]).resolve()
db_path = Path(os.environ["SARI_DB_PATH"]).resolve()

runtime_file = str(Path(sari.__file__).resolve())
if str((root_dir / "src").resolve()) in runtime_file:
    raise SystemExit(
        f"installed runtime check failed: imported from source tree ({runtime_file})"
    )
if "site-packages/sari" not in runtime_file:
    raise SystemExit(
        f"installed runtime check failed: expected site-packages path, got {runtime_file}"
    )

# Deterministic regression guard: duplicate relations must not crash flush.
regression_db = db_path.with_name("installed-freshdb-regression.db")
if regression_db.exists():
    regression_db.unlink()
init_schema(regression_db)
reg_repo = LspToolDataRepository(regression_db)
reg_repo.replace_file_data_many(
    [
        LspExtractPersistDTO(
            repo_id="regression",
            repo_root="/regression",
            relative_path="target.py",
            content_hash="h1",
            symbols=[],
            relations=[
                {
                    "from_symbol": "shared_name",
                    "to_symbol": "Target",
                    "line": 10,
                    "caller_relative_path": "caller_a.py",
                },
                {
                    "from_symbol": "shared_name",
                    "to_symbol": "Target",
                    "line": 10,
                    "caller_relative_path": "caller_b.py",
                },
            ],
            created_at="2026-03-24T00:00:00+00:00",
        )
    ]
)

config = AppConfig.default()
if Path(config.db_path).resolve() != db_path:
    raise SystemExit(
        f"SARI_DB_PATH mismatch: config={config.db_path}, env={db_path}"
    )

repos = build_repository_bundle(config.db_path)
workspace = WorkspaceService(repos.workspace_repo)
collection = build_file_collection_service_from_config(
    config=config,
    repos=repos,
    lsp_backend=None,
    run_mode=config.run_mode,
)

repo_root = str(root_dir)
try:
    workspace.add_workspace(repo_root)
except Exception:
    existing = repos.workspace_repo.get_by_path(repo_root)
    if existing is None:
        raise
    if not existing.is_active:
        repos.workspace_repo.set_active(repo_root, True)

scan = collection.scan_once(repo_root, trigger="manual")
loop_tail: list[dict[str, int]] = []
zero_rounds = 0
for i in range(120):
    n0 = int(collection.process_enrich_jobs(500))
    n2 = int(collection.process_enrich_jobs_l2(500))
    n3 = int(collection.process_enrich_jobs_l3(500))
    total = n0 + n2 + n3
    loop_tail.append({"i": i, "n0": n0, "n2": n2, "n3": n3, "total": total})
    zero_rounds = zero_rounds + 1 if total == 0 else 0
    if zero_rounds >= 6:
        break

with sqlite3.connect(config.db_path) as conn:
    pending_src_sari = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM collected_files_l1
            WHERE repo_root = ?
              AND relative_path LIKE 'src/sari/%'
              AND enrich_state = 'PENDING'
            """,
            (repo_root,),
        ).fetchone()[0]
    )
    symbols_src_sari = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM lsp_symbols
            WHERE repo_root = ?
              AND relative_path LIKE 'src/sari/%'
            """,
            (repo_root,),
        ).fetchone()[0]
    )

if pending_src_sari > 0:
    raise SystemExit(f"freshdb smoke failed: pending_src_sari={pending_src_sari}")
if symbols_src_sari <= 0:
    raise SystemExit(f"freshdb smoke failed: symbols_src_sari={symbols_src_sari}")

summary = {
    "runtime_version": sari.__version__,
    "runtime_file": runtime_file,
    "db_path": str(db_path),
    "scan": {
        "scanned_count": scan.scanned_count,
        "indexed_count": scan.indexed_count,
        "deleted_count": scan.deleted_count,
    },
    "pending_src_sari": pending_src_sari,
    "symbols_src_sari": symbols_src_sari,
    "loop_tail": loop_tail[-10:],
    "duplicate_relation_regression": "passed",
}
summary_path.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False))
PY

echo "[installed-freshdb-smoke] passed: ${SMOKE_SUMMARY_PATH}"
