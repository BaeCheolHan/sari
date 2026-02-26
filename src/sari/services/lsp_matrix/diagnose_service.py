"""LSP 매트릭스 결과 진단 서비스를 제공한다."""

from __future__ import annotations

import json
from pathlib import Path

from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.language.provision_policy import get_lsp_provision_policy


class LspMatrixDiagnoseService:
    """LSP 매트릭스 결과를 원인 분해 가능한 진단 리포트로 변환한다."""

    def diagnose(self, matrix_report: dict[str, object]) -> dict[str, object]:
        """매트릭스 리포트를 진단 요약으로 변환한다."""
        languages_raw = matrix_report.get("languages")
        if not isinstance(languages_raw, list):
            raise DaemonError(ErrorContext(code="ERR_LSP_MATRIX_INVALID_RESULT", message="languages must be list"))
        summary_raw = matrix_report.get("summary")
        summary = summary_raw if isinstance(summary_raw, dict) else {}
        gate_raw = matrix_report.get("gate")
        gate = gate_raw if isinstance(gate_raw, dict) else {}
        gate_run_raw = matrix_report.get("gate_run")
        gate_run = gate_run_raw if isinstance(gate_run_raw, dict) else {}

        missing_server_languages: list[str] = []
        timeout_languages: list[str] = []
        symbol_failed_languages: list[str] = []
        error_code_counts: dict[str, int] = {}
        language_policies: list[dict[str, str]] = []

        for item in languages_raw:
            if not isinstance(item, dict):
                continue
            language = str(item.get("language", "")).strip().lower()
            if language == "":
                continue
            error_code = str(item.get("last_error_code", "")).strip()
            if error_code != "":
                error_code_counts[error_code] = error_code_counts.get(error_code, 0) + 1
            policy = get_lsp_provision_policy(language)
            language_policies.append(
                {
                    "language": language,
                    "provisioning_mode": policy.provisioning_mode,
                    "install_hint": policy.install_hint,
                }
            )
            if error_code == "ERR_LSP_SERVER_MISSING":
                missing_server_languages.append(language)
            if error_code == "ERR_LSP_TIMEOUT" or bool(item.get("timeout_occurred")):
                timeout_languages.append(language)
            if not bool(item.get("symbol_extract_success", item.get("available", False))):
                symbol_failed_languages.append(language)

        gate_failed_symbols = gate.get("failed_symbol_languages")
        if isinstance(gate_failed_symbols, list):
            for item in gate_failed_symbols:
                if isinstance(item, str):
                    symbol_failed_languages.append(item.strip().lower())

        diagnosis = {
            "repo_root": str(matrix_report.get("repo_root", "")),
            "run_id": str(matrix_report.get("run_id", "")),
            "gate_decision": str(gate.get("gate_decision", "")),
            "gate_mode": str(gate_run.get("gate_mode", "")),
            "repair_applied": bool(gate_run.get("repair_applied", False)),
            "rerun_count": int(gate_run.get("rerun_count", 0)),
            "final_gate_decision": str(gate_run.get("final_gate_decision", str(gate.get("gate_decision", "")))),
            "readiness_percent": float(summary.get("readiness_percent", 0.0)),
            "symbol_extract_success_rate": float(summary.get("symbol_extract_success_rate", 0.0)),
            "missing_server_languages": sorted(set(missing_server_languages)),
            "timeout_languages": sorted(set(timeout_languages)),
            "symbol_failed_languages": sorted(set(symbol_failed_languages)),
            "error_code_counts": error_code_counts,
            "language_policies": sorted(language_policies, key=lambda item: item["language"]),
            "recommended_actions": self._build_recommended_actions(
                missing_server_languages=sorted(set(missing_server_languages)),
                timeout_languages=sorted(set(timeout_languages)),
                symbol_failed_languages=sorted(set(symbol_failed_languages)),
            ),
        }
        return diagnosis

    def render_markdown(self, diagnosis: dict[str, object]) -> str:
        """진단 JSON을 Markdown 보고서로 렌더링한다."""
        missing = _as_str_list(diagnosis.get("missing_server_languages"))
        timeout = _as_str_list(diagnosis.get("timeout_languages"))
        symbol_failed = _as_str_list(diagnosis.get("symbol_failed_languages"))
        actions = diagnosis.get("recommended_actions")
        action_lines: list[str] = []
        if isinstance(actions, list):
            for item in actions:
                if not isinstance(item, dict):
                    continue
                action_lines.append(
                    f"- [{item.get('severity', 'INFO')}] {item.get('title', '')}: {item.get('message', '')}"
                )
        if len(action_lines) == 0:
            action_lines.append("- [INFO] No action required")

        error_code_counts = diagnosis.get("error_code_counts")
        error_count_lines: list[str] = []
        if isinstance(error_code_counts, dict):
            for code, count in sorted(error_code_counts.items(), key=lambda x: x[0]):
                error_count_lines.append(f"- `{code}`: {int(count)}")
        if len(error_count_lines) == 0:
            error_count_lines.append("- none")
        policy_lines: list[str] = []
        policy_items = diagnosis.get("language_policies")
        if isinstance(policy_items, list):
            for item in policy_items:
                if not isinstance(item, dict):
                    continue
                language = str(item.get("language", "")).strip()
                mode = str(item.get("provisioning_mode", "")).strip()
                hint = str(item.get("install_hint", "")).strip()
                if language == "":
                    continue
                policy_lines.append(f"- `{language}` ({mode}): {hint}")
        if len(policy_lines) == 0:
            policy_lines.append("- none")

        return "\n".join(
            [
                "# LSP Matrix Diagnose Report",
                "",
                f"- repo_root: `{diagnosis.get('repo_root', '')}`",
                f"- run_id: `{diagnosis.get('run_id', '')}`",
                f"- gate_decision: `{diagnosis.get('gate_decision', '')}`",
                f"- gate_mode: `{diagnosis.get('gate_mode', '')}`",
                f"- repair_applied: `{diagnosis.get('repair_applied', False)}`",
                f"- rerun_count: `{diagnosis.get('rerun_count', 0)}`",
                f"- final_gate_decision: `{diagnosis.get('final_gate_decision', '')}`",
                f"- readiness_percent: `{diagnosis.get('readiness_percent', 0.0)}`",
                f"- symbol_extract_success_rate: `{diagnosis.get('symbol_extract_success_rate', 0.0)}`",
                "",
                "## Missing Servers",
                *(["- " + item for item in missing] if len(missing) > 0 else ["- none"]),
                "",
                "## Timeout Languages",
                *(["- " + item for item in timeout] if len(timeout) > 0 else ["- none"]),
                "",
                "## Symbol Failed Languages",
                *(["- " + item for item in symbol_failed] if len(symbol_failed) > 0 else ["- none"]),
                "",
                "## Error Code Counts",
                *error_count_lines,
                "",
                "## Provisioning Policies",
                *policy_lines,
                "",
                "## Recommended Actions",
                *action_lines,
                "",
            ]
        )

    def write_outputs(self, diagnosis: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
        """진단 결과를 JSON/Markdown 파일로 저장한다."""
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "lsp-matrix-diagnose.json"
        md_path = output_dir / "lsp-matrix-diagnose.md"
        json_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(self.render_markdown(diagnosis), encoding="utf-8")
        return json_path, md_path

    def _build_recommended_actions(
        self,
        missing_server_languages: list[str],
        timeout_languages: list[str],
        symbol_failed_languages: list[str],
    ) -> list[dict[str, str]]:
        """진단 결과 기반 권장 조치 목록을 생성한다."""
        actions: list[dict[str, str]] = []
        if len(missing_server_languages) > 0:
            hint_fragments: list[str] = []
            for language in missing_server_languages:
                policy = get_lsp_provision_policy(language)
                hint_fragments.append(f"{language}: {policy.install_hint}")
            actions.append(
                {
                    "severity": "HIGH",
                    "title": "Install Missing Language Servers",
                    "message": f"missing server languages: {', '.join(missing_server_languages)}",
                    "recovery_hint": " | ".join(hint_fragments),
                }
            )
        if len(timeout_languages) > 0:
            actions.append(
                {
                    "severity": "MEDIUM",
                    "title": "Review Timeout and Server Health",
                    "message": f"timeout languages: {', '.join(timeout_languages)}",
                }
            )
        if len(symbol_failed_languages) > 0:
            actions.append(
                {
                    "severity": "MEDIUM",
                    "title": "Review DocumentSymbol Extraction",
                    "message": f"symbol failed languages: {', '.join(symbol_failed_languages)}",
                }
            )
        if len(actions) == 0:
            actions.append(
                {
                    "severity": "INFO",
                    "title": "No Immediate Action",
                    "message": "all monitored languages passed current diagnose checks",
                }
            )
        return actions


def _as_str_list(value: object) -> list[str]:
    """값을 문자열 리스트로 정규화한다."""
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if token != "":
            normalized.append(token)
    return normalized
