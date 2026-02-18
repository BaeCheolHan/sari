# Batch-67 Queue Operations Closeout

## 목표
- queue 운영 경로(HTTP/CLI/service) 응답 메타를 일관화한다.
- release gate에 queue smoke를 포함해 운영 경로 회귀를 자동 감지한다.

## 구현
- `DeadJobActionResultDTO` 확장
  - `queue_snapshot`, `executed_at`, `repo_scope` 필드 추가
- `PipelineControlService`
  - `requeue_dead_jobs`, `purge_dead_jobs`가 스냅샷/실행시각/스코프를 포함해 반환
  - `get_queue_snapshot()` 추가
- `HTTP /pipeline/dead*`
  - limit 파싱 오류를 명시적으로 400 처리
  - `list/requeue/purge` 응답에 `meta` 추가
- `CLI pipeline dead *`
  - list/requeue/purge 출력에 `meta` 포함
- `tools/ci/run_release_gate.sh`
  - queue smoke (`pipeline dead list/requeue/purge`) 추가
  - summary에 `queue_ops_passed`/`logs.queue_ops` 추가

## 테스트
- `tests/unit/test_pipeline_control_service.py` 보강
  - `repo_scope`, `executed_at`, `queue_snapshot` 검증
- `tests/integration/test_daemon_http_integration.py` 보강
  - `/pipeline/dead`, `/pipeline/dead/requeue`, `/pipeline/dead/purge` 메타 검증
- `tests/unit/test_ci_release_gate_script.py` 보강
  - `queue_ops_passed`, `release-gate-queue-ops.log` 계약 검증

## 검증
- `pytest -q tests/integration/test_daemon_http_integration.py tests/unit/test_pipeline_control_service.py tests/unit/test_ci_release_gate_script.py tests/unit/test_ci_release_gate_mcp_probe.py`
  - 결과: `8 passed`
- `tools/ci/run_release_gate.sh`
  - 결과: `[release gate] passed`
- `artifacts/ci/release-gate-summary.json`
  - `queue_ops_passed: true` 확인
