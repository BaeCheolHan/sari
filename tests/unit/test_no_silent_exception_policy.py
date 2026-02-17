"""침묵 예외 금지 정책 검증 테스트."""

from __future__ import annotations

from pathlib import Path
import re


def test_no_except_pass_in_source_tree() -> None:
    """소스 전역에서 `except ...: pass` 패턴이 없어야 한다."""
    src_root = Path(__file__).resolve().parents[2] / "src"
    pattern = re.compile(r"except[^\n]*:\n[ \t]*pass\b")

    violations: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(src_root)}:{line_no}")

    assert violations == [], "침묵 예외 금지 위반:\n" + "\n".join(violations)


def test_no_broad_except_in_sari() -> None:
    """src/sari에는 broad-except가 없어야 한다."""
    sari_root = Path(__file__).resolve().parents[2] / "src" / "sari"
    broad_pattern = re.compile(r"except\s+(Exception|BaseException)\b|except\s*:\s*$")

    found: list[str] = []
    for path in sari_root.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        rel = path.relative_to(sari_root)
        for line_no, line in enumerate(lines, start=1):
            if broad_pattern.search(line) is not None:
                found.append(f"{rel}:{line_no}")

    assert found == [], "broad-except 금지 위반:\n" + "\n".join(sorted(found))
