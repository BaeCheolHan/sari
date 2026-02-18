#!/usr/bin/env bash
set -euo pipefail

TARGET_VERSION="${1:-}"

uv tool uninstall sari >/dev/null 2>&1 || true

if [[ -n "${TARGET_VERSION}" ]]; then
  uv tool install "sari==${TARGET_VERSION}"
else
  uv tool install sari
fi

uv tool list --show-paths | awk '/^sari v/{print}'
