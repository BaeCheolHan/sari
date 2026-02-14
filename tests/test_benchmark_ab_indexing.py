from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from tools.manual import benchmark_ab_indexing as ab


def test_parse_env_overrides_parses_and_validates():
    got = ab._parse_env_overrides(["A=1", "B=hello"])
    assert got == {"A": "1", "B": "hello"}

    try:
        ab._parse_env_overrides(["INVALID"])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_summarize_trials_computes_improvement_and_gates():
    trials = [
        {"mode": "A", "wall_s": 10.0, "cpu_s": 5.0, "maxrss_kib_delta": 1000, "files": 100, "symbols": 200, "relations": 300},
        {"mode": "A", "wall_s": 12.0, "cpu_s": 6.0, "maxrss_kib_delta": 1000, "files": 100, "symbols": 200, "relations": 300},
        {"mode": "B", "wall_s": 7.0, "cpu_s": 4.8, "maxrss_kib_delta": 1050, "files": 100, "symbols": 200, "relations": 300},
        {"mode": "B", "wall_s": 8.0, "cpu_s": 5.0, "maxrss_kib_delta": 1040, "files": 100, "symbols": 200, "relations": 300},
    ]
    summary = ab.summarize_trials(trials)
    assert summary["gates"]["integrity_ok"] is True
    assert summary["gates"]["load_guard_ok"] is True
    assert float(summary["improvement_pct"]["wall_s_median"]) > 0.0


def test_summarize_trials_detects_integrity_mismatch():
    trials = [
        {"mode": "A", "wall_s": 10.0, "cpu_s": 5.0, "maxrss_kib_delta": 1000, "files": 100, "symbols": 200, "relations": 300},
        {"mode": "B", "wall_s": 9.0, "cpu_s": 4.5, "maxrss_kib_delta": 900, "files": 99, "symbols": 200, "relations": 300},
    ]
    summary = ab.summarize_trials(trials)
    assert summary["gates"]["integrity_ok"] is False


def test_summarize_trials_file_scope_integrity_allows_symbol_diff():
    trials = [
        {"mode": "A", "wall_s": 10.0, "cpu_s": 5.0, "maxrss_kib_delta": 1000, "files": 100, "symbols": 200, "relations": 300},
        {"mode": "B", "wall_s": 6.0, "cpu_s": 3.0, "maxrss_kib_delta": 900, "files": 100, "symbols": 0, "relations": 0},
    ]
    summary = ab.summarize_trials(trials, integrity_scope="files")
    assert summary["gates"]["integrity_ok"] is True


def test_summarize_trials_includes_standby_and_full_wall_metrics():
    trials = [
        {
            "mode": "A",
            "standby_wall_s": 10.0,
            "full_wall_s": 10.0,
            "wall_s": 10.0,
            "cpu_s": 5.0,
            "maxrss_kib_delta": 1000,
            "files": 100,
            "symbols": 200,
            "relations": 300,
        },
        {
            "mode": "B",
            "standby_wall_s": 6.5,
            "full_wall_s": 8.0,
            "wall_s": 8.0,
            "cpu_s": 4.0,
            "maxrss_kib_delta": 900,
            "files": 100,
            "symbols": 200,
            "relations": 300,
        },
    ]

    summary = ab.summarize_trials(trials)
    mode_a = summary["mode_A"]
    mode_b = summary["mode_B"]
    assert float(mode_a["standby_wall_s_median"]) == 10.0
    assert float(mode_b["standby_wall_s_median"]) == 6.5
    assert float(mode_b["full_wall_s_median"]) == 8.0
    assert "standby_wall_s_median" in summary["improvement_pct"]
    assert "full_wall_s_median" in summary["improvement_pct"]
