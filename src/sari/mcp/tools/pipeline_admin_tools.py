"""MCP 파이프라인 운영 도구를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import SariBaseError
from sari.core.models import ErrorResponseDTO
from sari.mcp.tools.arg_parser import parse_optional_boolean, parse_optional_loose_int
from sari.mcp.tools.admin_tools import RepoValidationPort, validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline_control_service import PipelineControlService


def _service_error(exc: SariBaseError) -> dict[str, object]:
    """도메인 예외를 pack1 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class PipelinePolicyGetTool:
    """pipeline_policy_get MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파이프라인 정책을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        item = self._service.get_policy().to_dict()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelinePolicySetTool:
    """pipeline_policy_set MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파이프라인 정책을 갱신한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        deletion_hold, deletion_error = parse_optional_boolean(arguments=arguments, key="deletion_hold")
        if deletion_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message="invalid deletion_hold"))
        l3_p95_threshold_ms, l3_error = parse_optional_loose_int(arguments=arguments, key="l3_p95_threshold_ms")
        if l3_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=l3_error.message))
        dead_ratio_threshold_bps, dead_ratio_error = parse_optional_loose_int(arguments=arguments, key="dead_ratio_threshold_bps")
        if dead_ratio_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=dead_ratio_error.message))
        enrich_worker_count, workers_error = parse_optional_loose_int(arguments=arguments, key="workers")
        if workers_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=workers_error.message))
        watcher_queue_max, watcher_queue_max_error = parse_optional_loose_int(arguments=arguments, key="watcher_queue_max")
        if watcher_queue_max_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=watcher_queue_max_error.message))
        watcher_overflow_rescan_cooldown_sec, watcher_cooldown_error = parse_optional_loose_int(
            arguments=arguments,
            key="watcher_overflow_rescan_cooldown_sec",
        )
        if watcher_cooldown_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=watcher_cooldown_error.message))
        bootstrap_mode_enabled, bootstrap_mode_error = parse_optional_boolean(arguments=arguments, key="bootstrap_mode_enabled")
        if bootstrap_mode_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=bootstrap_mode_error.message))
        bootstrap_l3_worker_count, l3_workers_error = parse_optional_loose_int(arguments=arguments, key="bootstrap_l3_worker_count")
        if l3_workers_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=l3_workers_error.message))
        bootstrap_l3_queue_max, l3_queue_error = parse_optional_loose_int(arguments=arguments, key="bootstrap_l3_queue_max")
        if l3_queue_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=l3_queue_error.message))
        bootstrap_exit_min_l2_coverage_bps, l2_coverage_error = parse_optional_loose_int(
            arguments=arguments,
            key="bootstrap_exit_min_l2_coverage_bps",
        )
        if l2_coverage_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=l2_coverage_error.message))
        bootstrap_exit_max_sec, max_sec_error = parse_optional_loose_int(arguments=arguments, key="bootstrap_exit_max_sec")
        if max_sec_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=max_sec_error.message))
        alert_window_sec, alert_window_error = parse_optional_loose_int(arguments=arguments, key="alert_window_sec")
        if alert_window_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=alert_window_error.message))
        try:
            updated = self._service.update_policy(
                deletion_hold=deletion_hold,
                l3_p95_threshold_ms=l3_p95_threshold_ms,
                dead_ratio_threshold_bps=dead_ratio_threshold_bps,
                enrich_worker_count=enrich_worker_count,
                watcher_queue_max=watcher_queue_max,
                watcher_overflow_rescan_cooldown_sec=watcher_overflow_rescan_cooldown_sec,
                bootstrap_mode_enabled=bootstrap_mode_enabled,
                bootstrap_l3_worker_count=bootstrap_l3_worker_count,
                bootstrap_l3_queue_max=bootstrap_l3_queue_max,
                bootstrap_exit_min_l2_coverage_bps=bootstrap_exit_min_l2_coverage_bps,
                bootstrap_exit_max_sec=bootstrap_exit_max_sec,
                alert_window_sec=alert_window_sec,
            )
        except ValueError as exc:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message=str(exc)))
        except SariBaseError as exc:
            return _service_error(exc)
        item = updated.to_dict()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineAlertStatusTool:
    """pipeline_alert_status MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """알람 스냅샷을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        item = self._service.get_alert_status().to_dict()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineDeadListTool:
    """pipeline_dead_list MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """DEAD 작업 목록을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        limit, limit_error = parse_optional_loose_int(arguments=arguments, key="limit")
        if limit_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be int"))
        applied_limit = 20 if limit is None else limit
        try:
            items = self._service.list_dead_jobs(repo_root=repo, limit=applied_limit)
        except ValueError as exc:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message=str(exc)))
        except SariBaseError as exc:
            return _service_error(exc)
        return pack1_success(
            {
                "items": [item.to_dict() for item in items],
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineDeadRequeueTool:
    """pipeline_dead_requeue MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """DEAD 작업을 재큐잉한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        limit, limit_error = parse_optional_loose_int(arguments=arguments, key="limit")
        if limit_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be int"))
        all_scopes, all_error = parse_optional_boolean(arguments=arguments, key="all")
        if all_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="all must be bool"))
        applied_limit = 20 if limit is None else limit
        try:
            result = self._service.requeue_dead_jobs(
                repo_root=repo,
                limit=applied_limit,
                all_scopes=bool(all_scopes),
            )
        except ValueError as exc:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message=str(exc)))
        except SariBaseError as exc:
            return _service_error(exc)
        return pack1_success(
            {
                "items": [],
                "requeued_count": result.requeued_count,
                "meta": Pack1MetaDTO(
                    candidate_count=0,
                    resolved_count=result.requeued_count,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineDeadPurgeTool:
    """pipeline_dead_purge MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """DEAD 작업을 삭제한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])
        limit, limit_error = parse_optional_loose_int(arguments=arguments, key="limit")
        if limit_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be int"))
        all_scopes, all_error = parse_optional_boolean(arguments=arguments, key="all")
        if all_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="all must be bool"))
        applied_limit = 20 if limit is None else limit
        try:
            result = self._service.purge_dead_jobs(
                repo_root=repo,
                limit=applied_limit,
                all_scopes=bool(all_scopes),
            )
        except ValueError as exc:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message=str(exc)))
        except SariBaseError as exc:
            return _service_error(exc)
        return pack1_success(
            {
                "items": [],
                "purged_count": result.purged_count,
                "meta": Pack1MetaDTO(
                    candidate_count=0,
                    resolved_count=result.purged_count,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineAutoStatusTool:
    """pipeline_auto_status MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """자동제어 상태를 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        item = self._service.get_auto_control_state().to_dict()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineAutoSetTool:
    """pipeline_auto_set MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """자동제어 활성화를 설정한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        enabled, enabled_error = parse_optional_boolean(arguments=arguments, key="enabled")
        if enabled_error is not None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message="invalid enabled"))
        if enabled is None:
            return pack1_error(ErrorResponseDTO(code="ERR_POLICY_INVALID", message="enabled is required"))
        item = self._service.set_auto_hold_enabled(enabled).to_dict()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class PipelineAutoTickTool:
    """pipeline_auto_tick MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, service: PipelineControlService) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._service = service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """자동제어 평가를 1회 수행한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        item = self._service.evaluate_auto_hold()
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )
