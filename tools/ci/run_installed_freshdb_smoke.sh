#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${ROOT_DIR}/artifacts/ci"
WHEEL_DIR="${ARTIFACT_DIR}/wheelhouse"
SMOKE_DB_PATH="${ARTIFACT_DIR}/installed-freshdb-smoke.db"
SMOKE_SUMMARY_PATH="${ARTIFACT_DIR}/installed-freshdb-smoke-summary.json"

mkdir -p "${ARTIFACT_DIR}"
rm -rf "${WHEEL_DIR}"
rm -f "${SMOKE_DB_PATH}" "${SMOKE_DB_PATH}-wal" "${SMOKE_DB_PATH}-shm" "${SMOKE_SUMMARY_PATH}"
mkdir -p "${WHEEL_DIR}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[installed-freshdb-smoke] uv command not found." >&2
  exit 1
fi

# Always validate the actual installed CLI runtime from a built wheel.
uv build --wheel --out-dir "${WHEEL_DIR}" >/dev/null
WHEEL_PATH="$(ls -1 "${WHEEL_DIR}"/sari-*.whl | tail -n 1)"
if [[ -z "${WHEEL_PATH}" || ! -f "${WHEEL_PATH}" ]]; then
  echo "[installed-freshdb-smoke] wheel build output not found." >&2
  exit 1
fi
uv tool install --reinstall "${WHEEL_PATH}" >/dev/null

UV_TOOL_BIN_DIR="$(uv tool dir --bin)"
SARI_BIN="${UV_TOOL_BIN_DIR}/sari"
if [[ -z "${SARI_BIN}" ]]; then
  echo "[installed-freshdb-smoke] uv tool bin dir not resolved." >&2
  exit 1
fi
if [[ ! -x "${SARI_BIN}" ]]; then
  echo "[installed-freshdb-smoke] sari executable not found at uv tool bin dir." >&2
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
# Ensure source-tree imports never shadow installed runtime in this smoke.
unset PYTHONPATH || true

"${SARI_PY}" - <<'PY'
from __future__ import annotations

import json
import os
import sqlite3
import time
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
from sari.mcp.server import McpServer
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
stable_rounds = 0
deadline = time.time() + 180.0
for i in range(240):
    n0 = int(collection.process_enrich_jobs(500))
    n2 = int(collection.process_enrich_jobs_l2(500))
    n3 = int(collection.process_enrich_jobs_l3(500))
    n5 = int(collection.process_enrich_jobs_l5(500))
    total = n0 + n2 + n3 + n5
    with sqlite3.connect(config.db_path) as conn:
        pending_now = int(
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
        symbols_now = int(
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
    loop_tail.append(
        {
            "i": i,
            "n0": n0,
            "n2": n2,
            "n3": n3,
            "n5": n5,
            "total": total,
            "pending": pending_now,
            "symbols": symbols_now,
        }
    )
    if total == 0 and pending_now == 0 and symbols_now > 0:
        stable_rounds += 1
    else:
        stable_rounds = 0
    if stable_rounds >= 3:
        break
    if time.time() >= deadline:
        break
    if total == 0:
        time.sleep(0.2)

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
    relations_src_sari = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM lsp_call_relations
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

# MCP tool layer sanity check on installed runtime.
mcp_server = McpServer(db_path=config.db_path)
search_symbol_resp = mcp_server.handle_request(
    {
        "jsonrpc": "2.0",
        "id": 9101,
        "method": "tools/call",
        "params": {
            "name": "search_symbol",
            "arguments": {
                "repo": repo_root,
                "query": "status_endpoint",
                "limit": 5,
                "options": {"structured": 1},
            },
        },
    }
).to_dict()
search_symbol_items = (
    search_symbol_resp.get("result", {})
    .get("structuredContent", {})
    .get("items", [])
)
if not isinstance(search_symbol_items, list) or len(search_symbol_items) == 0:
    raise SystemExit(
        f"freshdb smoke failed: search_symbol(status_endpoint)=0, payload={search_symbol_resp}"
    )

# L5 relation flush can be empty in constrained environments; keep it as a signal in summary.
relations_signal = "ok" if relations_src_sari > 0 else "warn_empty_relations"

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
    "relations_src_sari": relations_src_sari,
    "relations_signal": relations_signal,
    "search_symbol_status_endpoint_count": len(search_symbol_items),
    "loop_tail": loop_tail[-10:],
    "duplicate_relation_regression": "passed",
}
summary_path.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False))
PY

echo "[installed-freshdb-smoke] passed: ${SMOKE_SUMMARY_PATH}"
