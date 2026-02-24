"""L3 AST vs LSP 품질 평가 서비스 검증."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.services.collection.l3_asset_loader import L3AssetLoader
from sari.services.collection.l3_quality_evaluation_service import (
    L3QualityEvaluationService,
)


def _sym(
    *,
    name: str,
    kind: str,
    line: int,
    end_line: int | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "kind": kind,
        "line": line,
        "end_line": line if end_line is None else end_line,
    }


def test_quality_eval_exact_match_returns_full_scores() -> None:
    service = L3QualityEvaluationService()
    ast = [_sym(name="Foo", kind="class", line=10), _sym(name="bar", kind="method", line=20)]
    lsp = [_sym(name="Foo", kind="class", line=10), _sym(name="bar", kind="method", line=20)]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.ast_symbol_count == 2
    assert result.lsp_symbol_count == 2
    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
    assert result.kind_match_rate == pytest.approx(1.0)
    assert result.position_match_rate == pytest.approx(1.0)
    assert result.quality_flags == ()


def test_quality_eval_applies_line_tolerance_for_position_match() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [_sym(name="foo", kind="function", line=10)]
    lsp = [_sym(name="foo", kind="function", line=12)]

    result = service.evaluate(language="python", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
    assert result.position_match_rate == pytest.approx(1.0)


def test_quality_eval_kind_mismatch_reduces_kind_match_rate() -> None:
    service = L3QualityEvaluationService()
    ast = [_sym(name="foo", kind="method", line=10)]
    lsp = [_sym(name="foo", kind="function", line=10)]

    result = service.evaluate(language="typescript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
    assert result.kind_match_rate == pytest.approx(0.0)
    assert "kind_mismatch_present" in result.quality_flags


def test_quality_eval_handles_empty_sets() -> None:
    service = L3QualityEvaluationService()

    both_empty = service.evaluate(language="java", ast_symbols=[], lsp_symbols=[])
    assert both_empty.symbol_recall_proxy == pytest.approx(1.0)
    assert both_empty.symbol_precision_proxy == pytest.approx(1.0)
    assert both_empty.kind_match_rate == pytest.approx(1.0)
    assert both_empty.position_match_rate == pytest.approx(1.0)

    lsp_only = service.evaluate(language="java", ast_symbols=[], lsp_symbols=[_sym(name="Foo", kind="class", line=1)])
    assert lsp_only.symbol_recall_proxy == pytest.approx(0.0)
    assert lsp_only.symbol_precision_proxy == pytest.approx(1.0)
    assert "ast_missing_symbols" in lsp_only.quality_flags

    ast_only = service.evaluate(language="java", ast_symbols=[_sym(name="Foo", kind="class", line=1)], lsp_symbols=[])
    assert ast_only.symbol_recall_proxy == pytest.approx(1.0)
    assert ast_only.symbol_precision_proxy == pytest.approx(0.0)
    assert "ast_extra_symbols" in ast_only.quality_flags


def test_quality_eval_normalizes_ts_method_style_kinds_to_same_bucket() -> None:
    service = L3QualityEvaluationService()
    ast = [_sym(name="foo", kind="method_definition", line=7)]
    lsp = [_sym(name="foo", kind="method", line=7)]

    result = service.evaluate(language="typescript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.kind_match_rate == pytest.approx(1.0)


def test_quality_eval_normalizes_lsp_symbolkind_numeric_strings_for_java() -> None:
    service = L3QualityEvaluationService()
    ast = [
        _sym(name="Foo", kind="class", line=3),
        _sym(name="bar", kind="method", line=10),
        _sym(name="name", kind="field", line=6),
    ]
    # LSP SymbolKind values often arrive as numeric strings in persisted rows.
    lsp = [
        _sym(name="Foo", kind="5", line=3),   # Class
        _sym(name="bar", kind="6", line=10),  # Method
        _sym(name="name", kind="8", line=6),  # Field
        _sym(name="STATUS", kind="14", line=7),  # Constant -> field
        _sym(name="ADMIN", kind="22", line=8),   # EnumMember -> field
    ]
    ast.extend(
        [
            _sym(name="STATUS", kind="field", line=7),
            _sym(name="ADMIN", kind="field", line=8),
        ]
    )

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
    assert result.kind_match_rate == pytest.approx(1.0)


def test_quality_eval_normalizes_java_names_and_ignores_synthetic_entries() -> None:
    service = L3QualityEvaluationService()
    ast = [
        _sym(name="OrderListBuilder", kind="class", line=10),
        _sym(name="run", kind="method", line=30),
    ]
    lsp = [
        _sym(name="OrderListBuilder<C extends OrderList>", kind="5", line=10),
        _sym(name="run(String name)", kind="6", line=30),
        _sym(name="batchSize$value", kind="8", line=40),
        _sym(name="new HashMap", kind="5", line=50),
    ]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_ignores_java_lombok_builder_like_symbols() -> None:
    service = L3QualityEvaluationService()
    ast = [
        _sym(name="OrderListDto", kind="class", line=10),
    ]
    lsp = [
        _sym(name="OrderListDto", kind="5", line=10),
        _sym(name="OrderListBuilder", kind="5", line=30),
        _sym(name="OrderListBuilderImpl", kind="5", line=31),
    ]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_ignores_java_duplicate_field_symbols_with_same_name() -> None:
    service = L3QualityEvaluationService()
    ast = [
        _sym(name="LedgerMasterCreate", kind="class", line=10),
        _sym(name="couponId", kind="field", line=13),
    ]
    lsp = [
        _sym(name="LedgerMasterCreate", kind="5", line=10),
        _sym(name="LedgerMasterCreateBuilder", kind="5", line=11),
        _sym(name="couponId", kind="8", line=13),  # real field
        _sym(name="couponId", kind="8", line=11),  # synthetic duplicate from builder context
    ]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_reports_java_missing_pattern_categories() -> None:
    service = L3QualityEvaluationService()
    ast = [_sym(name="Foo", kind="class", line=3)]
    lsp = [
        _sym(name="Foo", kind="5", line=3),  # class
        _sym(name="Foo", kind="9", line=6),  # constructor
        _sym(name="name", kind="8", line=9),  # field
        _sym(name="Outer.Inner", kind="5", line=12),  # nested type heuristic
    ]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(0.25)
    assert result.missing_patterns.count("missing_constructor") == 1
    assert result.missing_patterns.count("missing_field") == 1
    assert result.missing_patterns.count("missing_nested_type") == 1


def test_quality_eval_java_class_and_module_can_match_with_large_line_gap() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [
        _sym(name="kr.co.vendys.company.api", kind="module", line=1),
        _sym(name="QAuditable", kind="class", line=15),
    ]
    lsp = [
        _sym(name="kr.co.vendys.company.api", kind="4", line=1),   # package/module
        _sym(name="QAuditable", kind="5", line=12),                 # class (annotation offset)
    ]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_java_method_can_match_with_fallback_line_gap_override(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "queries").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text("{}", encoding="utf-8")
    (assets / "mappings" / "java.yaml").write_text(
        (
            '{"kind_bucket_map":{"6":"method","method":"method"},'
            '"capture_to_kind":{},'
            '"line_match_overrides":{"name_kind_fallback_buckets":["method"],"name_kind_fallback_max_line_gap":12}}'
        ),
        encoding="utf-8",
    )
    loader = L3AssetLoader(assets_root=assets)
    service = L3QualityEvaluationService(line_tolerance=2, asset_loader=loader)
    ast = [_sym(name="getCompany", kind="method", line=36)]
    lsp = [_sym(name="getCompany", kind="6", line=32)]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
    assert result.position_match_rate == pytest.approx(0.0)
    assert result.position_match_rate_relaxed == pytest.approx(1.0)


def test_quality_eval_fallback_line_gap_respects_max_limit(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "queries").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text("{}", encoding="utf-8")
    (assets / "mappings" / "java.yaml").write_text(
        (
            '{"kind_bucket_map":{"6":"method","method":"method"},'
            '"capture_to_kind":{},'
            '"line_match_overrides":{"name_kind_fallback_buckets":["method"],"name_kind_fallback_max_line_gap":12}}'
        ),
        encoding="utf-8",
    )
    loader = L3AssetLoader(assets_root=assets)
    service = L3QualityEvaluationService(line_tolerance=2, asset_loader=loader)
    ast = [_sym(name="getCompany", kind="method", line=80)]
    lsp = [_sym(name="getCompany", kind="6", line=32)]

    result = service.evaluate(language="java", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(0.0)
    assert result.symbol_precision_proxy == pytest.approx(0.0)


def test_quality_eval_uses_asset_missing_pattern_rules(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "queries").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text(
        '{"kind_bucket_map":{"class":"class"},"capture_to_kind":{}}',
        encoding="utf-8",
    )
    (assets / "mappings" / "python.yaml").write_text(
        (
            '{"kind_bucket_map":{"class":"class"},'
            '"capture_to_kind":{},'
            '"missing_pattern_rules":[{"when_kind_bucket_in":["class"],"result":"missing_py_class"}]}'
        ),
        encoding="utf-8",
    )
    loader = L3AssetLoader(assets_root=assets)
    service = L3QualityEvaluationService(asset_loader=loader)
    ast = []
    lsp = [_sym(name="Foo", kind="class", line=1)]

    result = service.evaluate(language="python", ast_symbols=ast, lsp_symbols=lsp)

    assert "missing_py_class" in result.missing_patterns


def test_quality_eval_javascript_uses_line_gap_fallback_override_from_assets() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [_sym(name="handler", kind="function", line=34)]
    lsp = [_sym(name="handler", kind="12", line=26)]  # function with larger line gap

    result = service.evaluate(language="javascript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_normalizes_javascript_callback_style_names() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [_sym(name="catch", kind="function", line=10), _sym(name="use", kind="function", line=20)]
    lsp = [
        _sym(name="promise.catch() callback", kind="12", line=10),
        _sym(name="app.use() callback", kind="12", line=20),
    ]

    result = service.evaluate(language="javascript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_normalizes_javascript_quoted_field_name() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [_sym(name="x-store", kind="field", line=10)]
    lsp = [_sym(name="'x-store'", kind="7", line=10)]

    result = service.evaluate(language="javascript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)


def test_quality_eval_matches_javascript_variable_kind_for_catch_parameter() -> None:
    service = L3QualityEvaluationService(line_tolerance=2)
    ast = [_sym(name="e", kind="variable", line=10)]
    lsp = [_sym(name="e", kind="13", line=10)]

    result = service.evaluate(language="javascript", ast_symbols=ast, lsp_symbols=lsp)

    assert result.symbol_recall_proxy == pytest.approx(1.0)
    assert result.symbol_precision_proxy == pytest.approx(1.0)
