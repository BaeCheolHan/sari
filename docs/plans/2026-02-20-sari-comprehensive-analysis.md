# Sari Repository Comprehensive Analysis Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Conduct a comprehensive analysis of the `sari` repository, covering structure, source code, database, and CI/CD, and generate a detailed report.

**Architecture:** Systematic exploration of the codebase, starting from configuration and moving inwards to core logic and supporting infrastructure.

**Tech Stack:** Python, Alembic, GitHub Actions, Pytest.

---

### Task 1: Project Metadata & Configuration Analysis

**Files:**
- Read: `pyproject.toml`
- Read: `README.md`
- Read: `alembic.ini`
- Read: `pytest.ini`
- Read: `.gitignore`

**Step 1: Inspect Configuration Files**
Read the content of the configuration files to understand dependencies, build tools, and project settings.

**Step 2: Summarize Project Metadata**
Identify the project's purpose, key dependencies, and build system.

### Task 2: Source Code Analysis - Core (`src/sari`)

**Files:**
- List: `src/sari/`
- Read: `src/sari/__main__.py`
- Read: `src/sari/cli/__init__.py` (and key files in `cli/`)
- List: `src/sari/core/`
- List: `src/sari/services/`
- List: `src/sari/db/`
- List: `src/sari/http/`
- List: `src/sari/lsp/`
- List: `src/sari/mcp/`

**Step 1: Analyze Entry Points**
Understand how the application starts and how the CLI is structured.

**Step 2: Analyze Core Services**
Identify the main business logic and services provided by the application.

**Step 3: Analyze Interfaces**
Examine the LSP, MCP, and HTTP interfaces to understand external interactions.

### Task 3: Source Code Analysis - Libraries (`src/solidlsp`, `src/sensai`, `src/serena`)

**Files:**
- List: `src/solidlsp/`
- Read: `src/solidlsp/__init__.py`
- List: `src/sensai/`
- List: `src/serena/`

**Step 1: Analyze `solidlsp`**
Understand the custom LSP implementation and its role.

**Step 2: Analyze Utility Libraries**
Determine the purpose and functionality of `sensai` and `serena`.

### Task 4: Database & Migrations Analysis

**Files:**
- List: `alembic/versions/`
- Read: `alembic/env.py`
- Read: Key model files in `src/sari/db/`

**Step 1: Review Migrations**
List and review migration scripts to understand the database schema evolution.

**Step 2: Analyze Data Models**
Examine the SQLAlchemy models to understand the current database structure.

### Task 5: Testing & CI/CD Analysis

**Files:**
- List: `tests/`
- Read: `tests/conftest.py`
- List: `.github/workflows/`
- Read: `.github/workflows/*.yml`

**Step 1: Analyze Test Structure**
Understand the testing strategy (unit vs. integration) and key test fixtures.

**Step 2: Analyze CI/CD Workflows**
Review GitHub Actions workflows to understand the build, test, and release pipelines.

### Task 6: Synthesis & Report Generation

**Files:**
- Create: `sari-analysis-report.md`

**Step 1: Compile Findings**
Synthesize all gathered information into a structured Markdown report.

**Step 2: detailed Report**
Write the final report with sections for Overview, Architecture, Database, Testing, and CI/CD.
