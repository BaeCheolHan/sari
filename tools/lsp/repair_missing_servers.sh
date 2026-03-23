#!/usr/bin/env bash
set -euo pipefail

# LSP diagnose JSON을 읽어 미설치 언어 서버 복구 명령을 안내/실행한다.
if [[ $# -lt 1 ]]; then
  echo "usage: $0 <diagnose-json-path> [--apply]" >&2
  exit 1
fi

DIAGNOSE_JSON="$1"
APPLY="false"
if [[ $# -ge 2 ]]; then
  if [[ "$2" == "--apply" ]]; then
    APPLY="true"
  else
    echo "unknown argument: $2" >&2
    exit 1
  fi
fi

if [[ ! -f "${DIAGNOSE_JSON}" ]]; then
  echo "diagnose json not found: ${DIAGNOSE_JSON}" >&2
  exit 1
fi

MISSING_LANGS=()
while IFS= read -r line; do
  [[ -n "${line}" ]] && MISSING_LANGS+=("${line}")
done < <(
  python3 - <<'PY' "${DIAGNOSE_JSON}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
langs = payload.get("missing_server_languages")
if not isinstance(langs, list):
    langs = []
if len(langs) == 0:
    fallback = payload.get("symbol_failed_languages")
    if isinstance(fallback, list):
        langs = fallback
for item in langs:
    if isinstance(item, str) and item.strip():
        print(item.strip().lower())
PY
)

if [[ ${#MISSING_LANGS[@]} -eq 0 ]]; then
  echo "[repair] missing_server_languages is empty"
  exit 0
fi

hint_for_language() {
  local lang="$1"
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  PYTHONPATH="${repo_root}/src:${PYTHONPATH:-}" python3 - <<'PY' "${lang}"
import sys
from sari.core.lsp_provision_policy import get_lsp_provision_policy

language = str(sys.argv[1]).strip().lower()
policy = get_lsp_provision_policy(language)
print(policy.install_hint)
PY
}

FAILED=0
for lang in "${MISSING_LANGS[@]}"; do
  hint="$(hint_for_language "${lang}")"
  echo "[repair] language=${lang}"
  echo "  hint: ${hint}"
  if [[ "${APPLY}" == "true" ]]; then
    case "${lang}" in
      python)
        python3 -m pip install pyrefly || FAILED=1
        ;;
      typescript)
        npm i -g typescript-language-server typescript || FAILED=1
        ;;
      go)
        go install golang.org/x/tools/gopls@latest || FAILED=1
        ;;
      vue)
        npm i -g @vue/language-server || FAILED=1
        ;;
      bash)
        npm i -g bash-language-server || FAILED=1
        ;;
      *)
        echo "  apply mode does not support automatic install for ${lang}" >&2
        FAILED=1
        ;;
    esac
  fi
done

if [[ "${APPLY}" == "false" ]]; then
  echo "[repair] dry-run completed. rerun with --apply to execute supported installs."
  exit 0
fi

if [[ ${FAILED} -ne 0 ]]; then
  echo "[repair] some installations failed. check messages above." >&2
  exit 1
fi

echo "[repair] apply completed successfully."
