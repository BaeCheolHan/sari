# Test Suite Conservative Dedup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 테스트 신뢰성을 유지하면서 완전 중복 테스트만 제거한다.

**Architecture:** 테스트 본문/검증식이 사실상 동일한 케이스만 정리하고, e2e/느린 테스트는 중복이 아닌 한 유지한다.

**Tech Stack:** Python, pytest, ast/hash 기반 정적 비교.

---

### Task 1: Duplicate Candidate Discovery

**Files:**
- Create: `docs/reports/2026-02-12-test-dedup-candidates.md`

**Step 1: 테스트 함수 목록/본문 유사도 추출**
Run: `python3 - <<'PY' ... PY`
Expected: 동일 본문 해시 기반 후보 목록 생성

**Step 2: 수동 검증**
Run: `rg -n "def test_" tests -S`
Expected: 후보가 실제로 완전 중복인지 확인

### Task 2: Conservative Dedup

**Files:**
- Modify: `tests/...` (중복으로 확정된 파일만)

**Step 1: failing/coverage 영향 없는 최소 편집**
- 완전 중복 케이스만 삭제 혹은 파라미터화 통합

**Step 2: 대상 테스트 재실행**
Run: `uv run pytest -q <affected tests>`
Expected: PASS

### Task 3: Verification + Report

**Files:**
- Create: `docs/reports/2026-02-12-test-dedup-results.md`

**Step 1: 회귀 확인**
Run: `uv run pytest -q tests/test_layer_boundary_contracts.py tests/test_ssot_registry_contracts.py`
Expected: PASS

**Step 2: 결과 기록**
- 삭제/통합 항목, 제외 사유(느리지만 의미 있음) 문서화
