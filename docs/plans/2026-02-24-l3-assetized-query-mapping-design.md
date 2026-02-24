# L3 AST Hardcoding Removal - Query/Mapping Assetization

## Goal
- Replace language-specific hardcoded extraction/quality rules in L3 with external query/mapping assets.
- Keep L1~L5 policy contracts unchanged.

## Implemented Scope
- Added asset loader:
  - `src/sari/services/collection/l3_asset_loader.py`
- Added asset files:
  - `src/sari/services/collection/assets/manifest.json`
  - `src/sari/services/collection/assets/queries/{python,typescript,java}/outline.scm`
  - `src/sari/services/collection/assets/mappings/{default,typescript,java}.yaml`
- Refactored extractor:
  - `src/sari/services/collection/l3_tree_sitter_outline.py`
  - Asset capture-to-kind map is applied.
  - `asset_mode=apply` prefers asset query.
- Refactored quality evaluator:
  - `src/sari/services/collection/l3_quality_evaluation_service.py`
  - kind bucket, missing pattern, line-match overrides are mapping-driven.
- Wired engine:
  - `src/sari/services/collection/enrich_engine.py`
  - `src/sari/services/collection/l3_treesitter_preprocess_service.py`
- Added runtime config:
  - `src/sari/core/config.py`
  - `l3_asset_mode`, `l3_asset_manifest_path`, `l3_asset_lang_allowlist`
- Added sync/validation tool:
  - `tools/l3_assets/sync_queries.py`

## Rollout Notes
- Default mode is `shadow`.
- `apply` mode enables asset query preference in extractor.
- Unknown/missing assets continue via existing fallback path.

## Verification
- `pytest -q tests/unit/test_l3_asset_loader.py tests/unit/test_l3_tree_sitter_outline.py tests/unit/test_l3_quality_evaluation_service.py tests/unit/test_l3_asset_sync_tool.py`
- `pytest -q tests/unit/test_batch17_performance_hardening.py tests/unit/test_l5_admission_policy.py tests/unit/test_pipeline_perf_service.py`
- `pytest -q tests/unit/test_arch_config_ssot_scope.py tests/unit/test_batch18_queue_and_config.py tests/unit/test_pipeline_auto_control.py tests/unit/test_http_pipeline_perf_endpoints.py`

## Checklist
- [x] Asset loader added and cached by language.
- [x] Query assets added for java/typescript/python.
- [x] Mapping assets added (default/java/typescript).
- [x] Extractor reads capture-to-kind from mapping.
- [x] Evaluator reads kind bucket / missing rules from mapping.
- [x] Asset sync validation tool added.
- [x] Unit tests added for loader and sync tool.
- [x] Existing pipeline tests pass after integration.
