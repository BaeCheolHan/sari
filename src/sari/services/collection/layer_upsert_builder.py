"""L3/L4/L5 layer upsert payload 생성 책임을 분리한다."""

from __future__ import annotations

from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessDecision, L3PreprocessResultDTO


class LayerUpsertBuilder:
    @staticmethod
    def _workspace_uid(repo_root: str) -> str:
        return repo_root.strip()

    def build_l3(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        symbols: list[dict[str, object]] = []
        degraded = False
        skipped_large_file = False
        if preprocess_result is not None:
            symbols = list(preprocess_result.symbols)
            degraded = bool(preprocess_result.degraded)
            skipped_large_file = preprocess_result.decision is L3PreprocessDecision.DEFERRED_HEAVY
        return {
            "workspace_id": self._workspace_uid(repo_root),
            "repo_root": repo_root,
            "relative_path": relative_path,
            "content_hash": content_hash,
            "symbols": symbols,
            "degraded": degraded,
            "l3_skipped_large_file": skipped_large_file,
            "updated_at": now_iso,
        }

    def build_l4(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO | None,
        admission_decision: L4AdmissionDecisionDTO | None,
        now_iso: str,
    ) -> dict[str, object]:
        if preprocess_result is None:
            decision_name = "needs_l5"
            source = "none"
            reason = "l3_preprocess_missing"
            symbol_count = 0
            degraded = True
        else:
            decision_name = preprocess_result.decision.value
            source = preprocess_result.source
            reason = preprocess_result.reason
            symbol_count = len(preprocess_result.symbols)
            degraded = bool(preprocess_result.degraded)
        confidence = 0.9 if (
            preprocess_result is not None
            and not degraded
            and preprocess_result.decision is L3PreprocessDecision.L3_ONLY
        ) else 0.35
        coverage = 0.0 if preprocess_result is not None and preprocess_result.decision is L3PreprocessDecision.DEFERRED_HEAVY else (0.6 if degraded else 1.0)
        ambiguity = max(0.0, min(1.0, 1.0 - confidence))
        normalized: dict[str, object] = {
            "decision": decision_name,
            "source": source,
            "reason": reason,
            "symbol_count": symbol_count,
            "admit_l5": bool(admission_decision.admit_l5) if admission_decision is not None else None,
            "reject_reason": admission_decision.reject_reason.value if admission_decision is not None and admission_decision.reject_reason is not None else None,
        }
        return {
            "workspace_id": self._workspace_uid(repo_root),
            "repo_root": repo_root,
            "relative_path": relative_path,
            "content_hash": content_hash,
            "normalized": normalized,
            "confidence": confidence,
            "ambiguity": ambiguity,
            "coverage": coverage,
            "updated_at": now_iso,
        }

    def build_l5(
        self,
        *,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        reason_code: L5ReasonCode,
        symbols: list[dict[str, object]],
        relations: list[dict[str, object]],
        now_iso: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": self._workspace_uid(repo_root),
            "repo_root": repo_root,
            "relative_path": relative_path,
            "content_hash": content_hash,
            "reason_code": reason_code.value,
            "semantics": {
                "source": "lsp",
                "symbols_count": len(symbols),
                "relations_count": len(relations),
            },
            "updated_at": now_iso,
        }
