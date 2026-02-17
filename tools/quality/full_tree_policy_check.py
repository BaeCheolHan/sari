"""전역 소스 트리 정책 준수 게이트를 점검한다."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PolicyViolationDTO:
    """정책 위반 1건을 표현한다."""

    file_path: str
    line_no: int
    rule: str
    message: str

    def to_dict(self) -> dict[str, object]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "file_path": self.file_path,
            "line_no": self.line_no,
            "rule": self.rule,
            "message": self.message,
        }


@dataclass(frozen=True)
class PolicyGateResultDTO:
    """정책 검사 결과를 표현한다."""

    scanned_files: int
    total_violations: int
    violations: list[PolicyViolationDTO]

    def to_dict(self) -> dict[str, object]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "scanned_files": self.scanned_files,
            "total_violations": self.total_violations,
            "violations": [item.to_dict() for item in self.violations],
        }


def run_policy_check(
    root: Path,
    max_lines_warn: int,
    max_lines_error: int,
    fail_on_todo: bool,
) -> PolicyGateResultDTO:
    """루트 하위 Python 파일을 스캔해 정책 위반을 수집한다."""
    python_files = sorted(root.rglob("*.py"))
    violations: list[PolicyViolationDTO] = []

    any_pattern = re.compile(r"\bAny\b|typing\.Any")
    broad_except_pattern = re.compile(r"except\s+(Exception|BaseException)\b|except\s*:\s*$")
    todo_pattern = re.compile(r"\b(TODO|FIXME|HACK)\b")

    for file_path in python_files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()

        # 파일 길이 정책
        line_count = len(lines)
        if line_count > max_lines_error:
            violations.append(
                PolicyViolationDTO(
                    file_path=str(file_path),
                    line_no=max_lines_error + 1,
                    rule="max_file_lines_error",
                    message=f"파일 길이 초과(라인={line_count}, 허용={max_lines_error})",
                )
            )
        elif line_count > max_lines_warn:
            violations.append(
                PolicyViolationDTO(
                    file_path=str(file_path),
                    line_no=max_lines_warn + 1,
                    rule="max_file_lines_warn",
                    message=f"파일 길이 경고(라인={line_count}, 권장={max_lines_warn})",
                )
            )

        for index, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Any 금지
            if any_pattern.search(line) is not None:
                violations.append(
                    PolicyViolationDTO(
                        file_path=str(file_path),
                        line_no=index,
                        rule="forbid_any",
                        message="Any 사용 금지 위반",
                    )
                )

            # broad-except 금지
            if broad_except_pattern.search(line) is not None:
                violations.append(
                    PolicyViolationDTO(
                        file_path=str(file_path),
                        line_no=index,
                        rule="forbid_broad_except",
                        message="broad except 금지 위반",
                    )
                )

            # except-pass 금지
            if stripped == "pass" and index > 1:
                previous = lines[index - 2]
                if previous.lstrip().startswith("except "):
                    violations.append(
                        PolicyViolationDTO(
                            file_path=str(file_path),
                            line_no=index,
                            rule="forbid_except_pass",
                            message="except-pass 침묵 예외 금지 위반",
                        )
                    )

            # TODO/HACK 금지(옵션)
            if fail_on_todo and todo_pattern.search(line) is not None:
                violations.append(
                    PolicyViolationDTO(
                        file_path=str(file_path),
                        line_no=index,
                        rule="forbid_todo_hack",
                        message="TODO/FIXME/HACK 금지 위반",
                    )
                )

    return PolicyGateResultDTO(
        scanned_files=len(python_files),
        total_violations=len(violations),
        violations=violations,
    )


def _write_markdown(path: Path, result: PolicyGateResultDTO) -> None:
    """정책 검사 결과를 Markdown 파일로 기록한다."""
    lines: list[str] = [
        "# Full Tree Policy Check",
        "",
        f"- scanned_files: {result.scanned_files}",
        f"- total_violations: {result.total_violations}",
        "",
        "## Violations",
    ]
    if len(result.violations) == 0:
        lines.append("- none")
    else:
        for item in result.violations:
            lines.append(f"- {item.rule}: {item.file_path}:{item.line_no} - {item.message}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """CLI 진입점이다."""
    parser = argparse.ArgumentParser(prog="full_tree_policy_check")
    parser.add_argument("--root", required=True, help="검사할 루트 경로")
    parser.add_argument("--output-json", required=False, default="", help="JSON 결과 파일")
    parser.add_argument("--output-md", required=False, default="", help="Markdown 결과 파일")
    parser.add_argument("--max-lines-warn", required=False, type=int, default=1200, help="라인 경고 임계치")
    parser.add_argument("--max-lines-error", required=False, type=int, default=1200, help="라인 실패 임계치")
    parser.add_argument("--fail-on-todo", action="store_true", help="TODO/FIXME/HACK 발견 시 실패")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit("root 경로를 찾을 수 없습니다")

    result = run_policy_check(
        root=root,
        max_lines_warn=max(1, int(args.max_lines_warn)),
        max_lines_error=max(1, int(args.max_lines_error)),
        fail_on_todo=bool(args.fail_on_todo),
    )
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if str(args.output_json).strip() != "":
        json_path = Path(str(args.output_json)).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if str(args.output_md).strip() != "":
        _write_markdown(Path(str(args.output_md)).expanduser().resolve(), result)

    return 0 if result.total_violations == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
