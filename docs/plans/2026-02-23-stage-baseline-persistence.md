# Stage Baseline Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stage A→B 판정에서 사용하는 `l4_admission_rate baseline_p50`를 영속 저장하고 재기동 후에도 동일 기준으로 판정되도록 연결한다.

**Architecture:** `pipeline_stage_baseline` SSOT 테이블/저장소를 추가한다. `PipelinePerfService`가 stage_exit 생성 시 baseline을 읽어 threshold를 계산하고, baseline이 없으면 관측값으로 초기화한다. 이후 daemon/cli/mcp 생성 경로에서 같은 DB 저장소를 주입한다.

**Tech Stack:** Python, SQLite, repository pattern, pytest.

---

### Task 1: Baseline 저장소 추가

**Files:**
- Modify: `src/sari/db/schema.py`
- Modify: `src/sari/db/migration.py`
- Create: `src/sari/db/repositories/pipeline_stage_baseline_repository.py`

### Task 2: Stage exit 판정식 연결

**Files:**
- Modify: `src/sari/services/pipeline_perf_service.py`
- Modify: `src/sari/daemon_process.py`
- Modify: `src/sari/cli/main.py`
- Modify: `src/sari/mcp/server.py`

### Task 3: 테스트 보강

**Files:**
- Modify: `tests/unit/test_pipeline_perf_service.py`
- Create or Modify: `tests/unit/test_pipeline_stage_baseline_repository.py`
- Run: `pytest -q tests/unit/test_pipeline_stage_baseline_repository.py tests/unit/test_pipeline_perf_service.py tests/unit/test_pipeline_auto_control.py tests/unit/test_http_pipeline_perf_endpoints.py`

### Task 4: 문서 체크리스트 반영

**Files:**
- Modify: `sari/Plan/2026-02-23/02. L1-L5 Execution Checklist v6.md`
