# Sari Repository Comprehensive Analysis Report

## 1. Project Overview

**Project Name:** `sari` (v2.0.14)
**Description:** A redesigned high-performance local search and indexing engine combined with an MCP (Model Context Protocol) Daemon.
**Goal:** Provide fast, local code search and symbol indexing for large repositories, exposing functionality via MCP to AI assistants (Gemini, Codex).
**Tech Stack:**
-   **Language:** Python 3.11+
-   **Build System:** `setuptools` (via `pyproject.toml`)
-   **Database:** SQLite (`state.db`) with `alembic` for migrations.
-   **Search Engine:** `tantivy` (Rust-based search engine binding).
-   **Web Framework:** `starlette` / `uvicorn` (ASGI).
-   **Testing:** `pytest`.

## 2. Architecture & Modules

The project follows a modular architecture, separating core domain logic, service layers, and external interfaces.

### 2.1 Core Logic (`src/sari/core`)
The `core` module houses the fundamental business logic and domain models.
-   **`config`**: Configuration management (`AppConfig`).
-   **`daemon_resolver` / `repo_identity`**: Logic for resolving daemon instances and repository identities.
-   **`language_registry`**: Manages supported languages and their properties.
-   **`scheduler`**: Handles background tasks and periodic jobs.
-   **`engine`**: (Empty in source tree, likely relies on `tantivy` bindings or external library integration).

### 2.2 Service Layer (`src/sari/services`)
Orchestrates business operations and coordinates between repositories and core logic.
-   **`workspace_service`**: Manages workspace roots (add, remove, activate).
-   **`daemon_service`**: Controls the daemon lifecycle (start, stop, status).
-   **`admin_service`**: Administrative tasks like indexing, doctor checks, and configuration generation.
-   **Pipeline Services**: A set of specialized services for processing and quality assurance:
    -   `pipeline_benchmark_service`: Benchmarks indexing performance.
    -   `pipeline_perf_service`: Measures system performance.
    -   `pipeline_quality_service`: Evaluates indexing quality.
    -   `pipeline_control_service`: Manages pipeline execution policies.
    -   `pipeline_lsp_matrix_service`: Validates LSP server readiness across languages.
    -   `language_probe_service`: Probes language server capabilities.

### 2.3 External Interfaces
-   **CLI (`src/sari/cli`)**: The primary entry point. Uses `click` to provide commands for workspace management (`roots`), daemon control (`daemon`), diagnostics (`doctor`), and pipeline operations.
-   **LSP (`src/sari/lsp`)**: Implements the Language Server Protocol client/hub logic.
    -   **`hub.py`**: Central hub for managing multiple LSP server instances.
    -   **`runtime_broker.py`**: Brokers runtime connections for LSP servers.
-   **MCP (`src/sari/mcp`)**: Implements the Model Context Protocol server.
    -   **`server.py`**: The MCP server implementation.
    -   **`proxy.py`**: Proxies MCP requests to the running daemon.
    -   **`daemon_router.py`**: Routes requests within the daemon.

### 2.4 Supporting Libraries
-   **`src/solidlsp`**: A dedicated library (vendored or standalone) for LSP server implementation details (`ls.py`, `ls_handler.py`, `ls_config.py`).
-   **`src/sensai`**: Utility library (minimal content observed).
-   **`src/serena`**: Text processing utilities (`text_utils.py`).

## 3. Database & Data Model

The project uses SQLite (`state.db`) managed by `alembic` migrations.

### 3.1 Schema Overview (`src/sari/db/schema.py`)
The schema is extensive, supporting workspaces, daemon state, file indexing, LSP data, and pipeline metrics.

### 3.2 Key Tables
-   **Workspace & Runtime**:
    -   `workspaces`: Managed repository paths.
    -   `daemon_runtime`: Current daemon process state (PID, host, port).
    -   `daemon_registry`: Registry of active/known daemons.
-   **File Indexing**:
    -   `collected_files_l1`: L1 cache for file metadata (mtime, size, hash).
    -   `collected_file_bodies_l2`: L2 cache for file content (zlib compressed).
    -   `file_enrich_queue`: Job queue for background enrichment/indexing.
    -   `candidate_index_changes`: Tracks pending changes for the index.
-   **LSP & Symbols**:
    -   `lsp_symbols`: Indexed symbols (name, kind, location).
    -   `lsp_call_relations`: Call graph data (from -> to).
    -   `lsp_symbol_cache`: Caches symbol search results.
    -   `language_probe_status`: Status of language servers.
-   **Pipelines & Metrics**:
    -   `pipeline_policy`, `pipeline_control_state`: Configuration for pipelines.
    -   `pipeline_job_events`, `pipeline_error_events`: Event logging.
    -   `pipeline_benchmark_runs`, `pipeline_perf_runs`, `pipeline_quality_runs`: Test execution records.
-   **Knowledge Base**:
    -   `snippet_entries`: Code snippets.
    -   `knowledge_entries`: Higher-level knowledge extraction.
    -   `file_embeddings`, `query_embeddings`: Vector embeddings for semantic search.

### 3.3 Migrations (`alembic/versions`)
Migration scripts are named by date and feature, e.g., `20260216_0001_baseline.py`, `20260219_0006_repo_id_ssot.py`. This indicates an active development cycle with frequent schema updates.

## 4. DevOps & Tooling

### 4.1 Build System
-   Managed via `pyproject.toml`.
-   Dependencies include `peewee`, `sqlalchemy`, `alembic`, `pydantic`, `structlog`, `starlette`, `uvicorn`, `tantivy`, `watchdog`.

### 4.2 Testing Strategy
-   **Framework**: `pytest`.
-   **Structure**:
    -   `unit`: Unit tests for individual components (e.g., `test_admin_service.py`, `test_daemon_db_fail_fast.py`).
    -   `integration`: End-to-end tests (e.g., `test_lifecycle_e2e.py`, `test_cli_lsp_matrix_process_e2e.py`).
    -   `snapshots`: Expected output snapshots for regression testing.

### 4.3 CI/CD Workflows (`.github/workflows`)
-   **`release-pypi.yml`**: Automates PyPI releases on tag push (`v*`). Includes a "release gate" script (`tools/ci/run_release_gate.sh`) and version verification.
-   **`lsp-matrix-pr-gate.yml`**: Ensures LSP compatibility and stability on Pull Requests.
-   **`mcp-soak-nightly.yml`**: Runs nightly soak tests to detect memory leaks or long-running issues.

## 5. Conclusion
`sari` is a sophisticated, modular system designed for robustness and performance. It combines a local search engine with an MCP interface, supported by a complex pipeline architecture for quality assurance and benchmarking. The codebase is well-structured with clear separation of concerns, extensive testing, and automated CI/CD processes.
