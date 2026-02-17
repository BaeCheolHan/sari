# Real LSP E2E Runbook

## 목적
- 실환경에서 LSP 매트릭스 품질을 반복 측정한다.
- 미설치 언어 서버를 빠르게 복구하고 재측정한다.
- PR/Main 운영 게이트를 동일 스크립트로 일관되게 실행한다.

## 운영 모드
- PR: report-only (실패 리포트 생성, 파이프라인 성공 유지)
- main/schedule: hard gate (실패 시 파이프라인 실패)
- 공통: 미설치 서버가 감지되면 자동 복구(`--apply`) 후 1회 재실행한다.

## CI 게이트 실행
```bash
tools/ci/run_lsp_matrix_gate.sh --report-only true   # PR
tools/ci/run_lsp_matrix_gate.sh --report-only false  # main/schedule
```

- 산출물:
  - `artifacts/ci/lsp-matrix-cli.log`
  - `artifacts/ci/lsp-matrix-report.json`
  - `artifacts/ci/lsp-matrix-diagnose.json`
  - `artifacts/ci/lsp-matrix-diagnose.md`
  - `artifacts/ci/lsp-matrix-gate-summary.json`

- `lsp-matrix-gate-summary.json` 핵심 필드:
  - `run_id`
  - `gate_mode` (`report-only` or `hard`)
  - `repair_applied` (bool)
  - `rerun_count` (0 or 1)
  - `final_gate_decision` (`PASS`/`FAIL`)

## 1) 매트릭스 실행
```bash
PYTHONPATH=src python3 -m sari.cli.main pipeline lsp-matrix run \
  --repo /absolute/repo/path \
  --fail-on-unavailable false \
  --strict-all-languages true \
  --strict-symbol-gate true
```

## 2) 진단 리포트 생성
```bash
PYTHONPATH=src python3 -m sari.cli.main pipeline lsp-matrix diagnose \
  --repo /absolute/repo/path \
  --mode latest \
  --output-dir artifacts/ci
```

- 출력 파일
  - `artifacts/ci/lsp-matrix-diagnose.json`
  - `artifacts/ci/lsp-matrix-diagnose.md`

## 3) 미설치 서버 복구
```bash
tools/lsp/repair_missing_servers.sh artifacts/ci/lsp-matrix-diagnose.json
```

- 실제 설치 실행(지원 언어만 자동 설치):
```bash
tools/lsp/repair_missing_servers.sh artifacts/ci/lsp-matrix-diagnose.json --apply
```

## 4) 재측정
- 1)~2) 명령을 다시 실행해 `missing_server_languages` 감소 여부를 확인한다.

## 운영 원칙
- 침묵 예외 없이 모든 실패는 명시 코드로 관찰한다.
- PR은 report-only, main/schedule은 hard gate를 기본으로 한다.
