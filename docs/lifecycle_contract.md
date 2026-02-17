# Daemon Lifecycle Contract

## Status Contract
`GET /status` 응답은 아래 lifecycle 필드를 포함한다.

- `daemon.last_heartbeat_at`: 데몬 heartbeat 최신 ISO8601 시각
- `daemon.last_exit_reason`: 마지막 종료 사유(`string | null`)
- `daemon_lifecycle.last_heartbeat_at`: lifecycle 뷰 heartbeat 시각
- `daemon_lifecycle.heartbeat_age_sec`: 현재 시각 기준 heartbeat 경과 초(`float`)
- `daemon_lifecycle.last_exit_reason`: lifecycle 뷰 종료 사유

## Exit Reason Codes
- `NORMAL_SHUTDOWN`: 정상 종료(SIGTERM)
- `FORCE_KILLED`: grace timeout 이후 강제 종료(SIGKILL)
- `ORPHAN_SELF_TERMINATE`: 부모 상실 감지 후 자가 종료
- `AUTO_LOOP_FAILURE`: dev 모드 auto-loop 실패로 종료
- `LSP_STOP_FAILURE`: shutdown 중 LSP stop 실패

## Runtime Persistence
- 현재 런타임은 `daemon_runtime` 단일 레코드에 저장한다.
- 종료 이벤트 이력은 `daemon_runtime_history`에 append 한다.
- stale runtime 정리는 `last_heartbeat_at` 기준 timeout 정책으로 수행한다.
