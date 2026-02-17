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

mapfile -t MISSING_LANGS < <(
  python3 - <<'PY' "${DIAGNOSE_JSON}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
langs = payload.get("missing_server_languages")
if not isinstance(langs, list):
    raise SystemExit(1)
for item in langs:
    if isinstance(item, str) and item.strip():
        print(item.strip().lower())
PY
)

if [[ ${#MISSING_LANGS[@]} -eq 0 ]]; then
  echo "[repair] missing_server_languages is empty"
  exit 0
fi

declare -A INSTALL_HINTS
INSTALL_HINTS[python]="npm i -g pyright"
INSTALL_HINTS[typescript]="npm i -g typescript-language-server typescript"
INSTALL_HINTS[java]="install eclipse-jdtls (package manager or official release)"
INSTALL_HINTS[kotlin]="install kotlin-language-server"
INSTALL_HINTS[go]="go install golang.org/x/tools/gopls@latest"
INSTALL_HINTS[rust]="install rust-analyzer"
INSTALL_HINTS[csharp]="install omnisharp/roslyn language server"
INSTALL_HINTS[ruby]="gem install ruby-lsp"
INSTALL_HINTS[php]="install intelephense or phpactor language server"
INSTALL_HINTS[vue]="npm i -g @vue/language-server"
INSTALL_HINTS[bash]="npm i -g bash-language-server"

FAILED=0
for lang in "${MISSING_LANGS[@]}"; do
  hint="${INSTALL_HINTS[$lang]:-manual install required for language: ${lang}}"
  echo "[repair] language=${lang}"
  echo "  hint: ${hint}"
  if [[ "${APPLY}" == "true" ]]; then
    case "${lang}" in
      python)
        npm i -g pyright || FAILED=1
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
