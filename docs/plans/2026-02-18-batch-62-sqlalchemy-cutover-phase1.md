# Batch-62 SQLAlchemy Cutover Phase-1

## 목표
- Core 저장소에서 sqlite `connect` 직접 의존을 줄이고 SQLAlchemy 세션 팩토리 기반으로 전환한다.
- 데몬/CLI/MCP 진입점에서 공통 세션 팩토리를 주입한다.
- doctor에 실제 ORM 상태를 노출한다.

## 구현 체크리스트
- [x] `src/sari/db/session.py` 추가 (engine/session factory + SQLite pragma)
- [x] `WorkspaceRepository` SQLAlchemy 전환
- [x] `RuntimeRepository` SQLAlchemy 전환
- [x] `FileCollectionRepository` SQLAlchemy 전환
- [x] `CandidateIndexChangeRepository` SQLAlchemy 전환
- [x] `FileEnrichQueueRepository` SQLAlchemy 전환
- [x] `daemon_process.py` 공통 session factory 주입
- [x] `cli/main.py` 공통 session factory 주입
- [x] `mcp/server.py` 공통 session factory 주입
- [x] `AdminService.doctor` ORM 상태를 정적값이 아닌 실제 상태로 보고

## 결과
- 저장소 `connect` 직접 의존 수: `21 -> 16`
- doctor 출력: `mixed(sqlalchemy+sqlite):legacy_repositories=16`

## 검증
- `PYTHONPATH=src python3 -m pytest -q tests/unit/test_candidate_index_change_repository.py tests/unit/test_batch17_performance_hardening.py tests/unit/test_file_collection_soc_delegation.py tests/unit/test_cli_admin_commands.py tests/unit/test_mcp_server_protocol.py`
  - 결과: `45 passed`
- `PYTHONPATH=src python3 -m pytest -q tests/integration/test_daemon_http_integration.py tests/unit/test_daemon_resolver_and_proxy.py`
  - 결과: `19 passed`

## 다음 배치(Phase-2)
1. 나머지 repository 군 전환 및 `connect` 의존 0화
2. doctor를 `sqlalchemy_only`로 수렴
3. 세션 팩토리 타입 정합성 강화(Protocol 기반)
