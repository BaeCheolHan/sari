# Batch-61 Candidate Write Decouple Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 검색 경로의 Tantivy 쓰기 경합을 줄이기 위해 pending 변경 반영을 백그라운드 플러시 경로로 분리한다.

**Architecture:** `TantivyCandidateBackend`에 `flush_pending_changes` 전용 경로를 추가하고, `search()`의 pending apply를 옵션화한다. 데몬 기본 조합에서는 search write를 비활성화하고 `FileCollectionService -> RuntimeManager` 루프에서 주기 플러시를 수행한다.

**Tech Stack:** Python 3.11+, Tantivy, SQLite, pytest

---

## Checklist
- [x] `TantivyCandidateBackend`에 `apply_pending_on_search` 옵션 추가
- [x] `TantivyCandidateBackend.flush_pending_changes()` 추가
- [x] `CandidateSearchService.build_default()`에 옵션 전달 경로 추가
- [x] `CandidateSearchService.flush_pending_changes()` 추가
- [x] `RuntimeManager`에 candidate flush 콜백 연결
- [x] `FileCollectionService._flush_candidate_index_changes()` 추가 및 명시 오류 승격
- [x] daemon/mcp 기본 조합에서 `apply_pending_on_search=False` 적용
- [x] 단위/통합 테스트 보강 및 통과

## Verification
- `PYTHONPATH=src python3 -m pytest -q tests/unit/test_batch17_performance_hardening.py tests/unit/test_file_collection_soc_delegation.py tests/unit/test_candidate_search_backends.py`
  - 결과: `28 passed`
- `PYTHONPATH=src python3 -m pytest -q tests/integration/test_daemon_http_integration.py tests/unit/test_mcp_server_protocol.py`
  - 결과: `11 passed`
