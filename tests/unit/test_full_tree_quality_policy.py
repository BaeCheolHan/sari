"""전역 품질 정책 게이트 스크립트를 검증한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module(module_path: Path, module_name: str) -> object:
    """파일 경로 기반으로 모듈을 동적으로 로드한다."""
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module spec load failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_policy_check_detects_any_broad_except_and_todo(tmp_path: Path) -> None:
    """정책 위반 샘플을 스캔하면 위반이 탐지되어야 한다."""
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "bad.py").write_text(
        "from typing import Any\n"
        "def bad(x: Any) -> int:\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        pass\n"
        "    # TODO remove me\n"
        "    return 0\n",
        encoding="utf-8",
    )

    script_path = Path(__file__).resolve().parents[2] / "tools" / "quality" / "full_tree_policy_check.py"
    module = _load_module(script_path, "full_tree_policy_check")
    result = module.run_policy_check(src_root, max_lines_warn=5, max_lines_error=7, fail_on_todo=True)
    rules = {item.rule for item in result.violations}

    assert result.total_violations > 0
    assert "forbid_any" in rules
    assert "forbid_broad_except" in rules
    assert "forbid_except_pass" in rules
    assert "forbid_todo_hack" in rules
    assert "max_file_lines_error" in rules


def test_policy_check_passes_clean_source(tmp_path: Path) -> None:
    """정책 위반이 없는 샘플은 통과해야 한다."""
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "good.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n",
        encoding="utf-8",
    )

    script_path = Path(__file__).resolve().parents[2] / "tools" / "quality" / "full_tree_policy_check.py"
    module = _load_module(script_path, "full_tree_policy_check_clean")
    result = module.run_policy_check(src_root, max_lines_warn=50, max_lines_error=100, fail_on_todo=True)

    assert result.total_violations == 0
