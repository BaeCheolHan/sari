# PyPI Release Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** GitHub Actions 기반으로 `sari`를 태그/수동 실행 시 PyPI에 자동 배포한다.

**Architecture:** 로컬 배포 스크립트에서 빌드/검증 계약을 고정하고, GitHub Actions에서 동일 계약을 호출한 뒤 Trusted Publishing으로 업로드한다. 태그 릴리스와 수동 배포를 모두 지원한다.

**Tech Stack:** GitHub Actions, PyPA build/twine, pypa/gh-action-pypi-publish

---

### Task 1: RED - 배포 워크플로 계약 테스트 추가

**Files:**
- Create: `tests/unit/test_ci_pypi_release_workflow.py`

### Task 2: GREEN - 배포 스크립트/워크플로 구현

**Files:**
- Create: `tools/ci/release_pypi.sh`
- Create: `.github/workflows/release-pypi.yml`

### Task 3: 검증 및 문서 반영

**Files:**
- Modify: `README.md`
- Modify: `sari-rebuild/02. Execution Checklist.md` (Obsidian)
- Modify: `sari-rebuild/03. Decision Log.md` (Obsidian)
- Modify: `sari-rebuild/04. Test & Verification Log.md` (Obsidian)
