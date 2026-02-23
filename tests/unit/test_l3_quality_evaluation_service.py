"""L3 AST vs LSP 품질 평가 서비스 검증."""

from __future__ import annotations

import pytest

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

