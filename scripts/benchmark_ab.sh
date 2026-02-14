#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACES="${1:-}"
REPEATS="${2:-5}"
OUT_DIR="${3:-}"

if [[ -z "$WORKSPACES" ]]; then
  echo "Usage: scripts/benchmark_ab.sh <workspace_paths_csv> [repeats=5] [out_dir]"
  echo "Example: scripts/benchmark_ab.sh /path/ws1,/path/ws2 5"
  exit 1
fi

MODE_A_ENV="${MODE_A_ENV:-SARI_INDEXER_ADAPTIVE_FLUSH=0}"
MODE_B_ENV="${MODE_B_ENV:-SARI_INDEXER_ADAPTIVE_FLUSH=1}"
INTEGRITY_SCOPE="${INTEGRITY_SCOPE:-full}"
MODE_B_BACKFILL_FULL="${MODE_B_BACKFILL_FULL:-0}"

CMD=(
  uv run python tools/manual/benchmark_ab_indexing.py
  --repeats "$REPEATS"
  --integrity-scope "$INTEGRITY_SCOPE"
)

IFS=',' read -r -a WS_ITEMS <<< "$WORKSPACES"
for item in "${WS_ITEMS[@]}"; do
  ws="${item#"${item%%[![:space:]]*}"}"
  ws="${ws%"${ws##*[![:space:]]}"}"
  [[ -n "$ws" ]] && CMD+=(--workspace "$ws")
done

if [[ -n "$OUT_DIR" ]]; then
  CMD+=(--out-dir "$OUT_DIR")
fi
if [[ "$MODE_B_BACKFILL_FULL" == "1" || "$MODE_B_BACKFILL_FULL" == "true" || "$MODE_B_BACKFILL_FULL" == "yes" ]]; then
  CMD+=(--mode-b-backfill-full)
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
echo "[A/B] MODE_B_BACKFILL_FULL=$MODE_B_BACKFILL_FULL"
"${CMD[@]}"
