"""HTTP 검색 progress 메타 안전 변환을 검증한다."""

from __future__ import annotations

from types import SimpleNamespace

from sari.http.search_endpoints import _search_progress_meta


class _Metrics:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _FileCollectionService:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def get_pipeline_metrics(self) -> _Metrics:
        return _Metrics(self._payload)


def test_search_progress_meta_returns_defaults_for_invalid_payload_types() -> None:
    context = SimpleNamespace(
        file_collection_service=_FileCollectionService(
            {
                "progress_percent_l2": object(),
                "progress_percent_l3": None,
                "eta_l2_sec": "not-int",
                "eta_l3_sec": "",
                "remaining_jobs_l2": None,
                "remaining_jobs_l3": object(),
                "worker_state": object(),
            }
        )
    )

    result = _search_progress_meta(context)  # type: ignore[arg-type]

    assert result == {
        "progress_percent_l2": 0.0,
        "progress_percent_l3": 0.0,
        "eta_l2_sec": -1,
        "eta_l3_sec": -1,
        "remaining_jobs_l2": 0,
        "remaining_jobs_l3": 0,
        "worker_state": "unknown",
    }


def test_search_progress_meta_parses_numeric_strings() -> None:
    context = SimpleNamespace(
        file_collection_service=_FileCollectionService(
            {
                "progress_percent_l2": "12.5",
                "progress_percent_l3": "7",
                "eta_l2_sec": "100",
                "eta_l3_sec": "200",
                "remaining_jobs_l2": "11",
                "remaining_jobs_l3": "22",
                "worker_state": "running",
            }
        )
    )

    result = _search_progress_meta(context)  # type: ignore[arg-type]

    assert result == {
        "progress_percent_l2": 12.5,
        "progress_percent_l3": 7.0,
        "eta_l2_sec": 100,
        "eta_l3_sec": 200,
        "remaining_jobs_l2": 11,
        "remaining_jobs_l3": 22,
        "worker_state": "running",
    }
