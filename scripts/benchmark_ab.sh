#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_PATH="${1:-}"
REPEATS="${2:-5}"
OUT_DIR="${3:-}"

if [[ -z "$WORKSPACE_PATH" ]]; then
  echo "Usage: scripts/benchmark_ab.sh <workspace_path> [repeats=5] [out_dir]"
  exit 1
fi

MODE_A_ENV="${MODE_A_ENV:-SARI_INDEXER_ADAPTIVE_FLUSH=0}"
MODE_B_ENV="${MODE_B_ENV:-SARI_INDEXER_ADAPTIVE_FLUSH=1}"
INTEGRITY_SCOPE="${INTEGRITY_SCOPE:-full}"

CMD=(
  uv run python tools/manual/benchmark_ab_indexing.py
  --workspace "$WORKSPACE_PATH"
  --repeats "$REPEATS"
  --integrity-scope "$INTEGRITY_SCOPE"
)

if [[ -n "$OUT_DIR" ]]; then
  CMD+=(--out-dir "$OUT_DIR")
fi

IFS=',' read -r -a A_ITEMS <<< "$MODE_A_ENV"
for item in "${A_ITEMS[@]}"; do
  [[ -n "${item// }" ]] && CMD+=(--mode-a-env "$item")
done

IFS=',' read -r -a B_ITEMS <<< "$MODE_B_ENV"
for item in "${B_ITEMS[@]}"; do
  [[ -n "${item// }" ]] && CMD+=(--mode-b-env "$item")
done

cd "$ROOT_DIR"
echo "[A/B] MODE_A_ENV=$MODE_A_ENV"
echo "[A/B] MODE_B_ENV=$MODE_B_ENV"
echo "[A/B] INTEGRITY_SCOPE=$INTEGRITY_SCOPE"
"${CMD[@]}"
