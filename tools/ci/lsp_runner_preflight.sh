#!/usr/bin/env bash
set -euo pipefail

# 자가호스티드 러너 필수 런타임/도구 존재 여부를 검증한다.
required_bins=(
  python3
  node
  npm
  java
  javac
  go
  rustc
  cargo
  dotnet
  swift
  ruby
  php
  R
  perl
  julia
  pwsh
)

missing=()
for bin_name in "${required_bins[@]}"; do
  if ! command -v "${bin_name}" >/dev/null 2>&1; then
    missing+=("${bin_name}")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "[LSP preflight] 필수 도구 누락: ${missing[*]}" >&2
  exit 1
fi

echo "[LSP preflight] 필수 도구 검증 통과"
