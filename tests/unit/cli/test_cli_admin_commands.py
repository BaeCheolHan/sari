"""CLI 운영 명령의 출력 계약을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from click.testing import CliRunner
from pytest import MonkeyPatch

from sari.cli.main import cli


def _prepare_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI 기본 설정 경로가 임시 디렉터리를 사용하도록 HOME을 설정한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)


def _load_cli_schema() -> dict[str, object]:
    """CLI 출력 스키마 파일을 읽어 파싱한다."""
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "cli_output_schema.json"
    return cast(dict[str, object], json.loads(schema_path.read_text(encoding="utf-8")))


def _resolve_ref(schema_root: dict[str, object], ref: str) -> dict[str, object]:
    """$ref 경로를 루트 스키마에서 해석한다."""
    if not ref.startswith("#/$defs/"):
        raise AssertionError(f"지원하지 않는 ref 형식: {ref}")
    def_name = ref.split("/", 2)[2]
    defs = cast(dict[str, object], schema_root["$defs"])
    target = defs.get(def_name)
    assert isinstance(target, dict)
    return cast(dict[str, object], target)


def _assert_schema(payload: object, schema: dict[str, object], schema_root: dict[str, object]) -> None:
    """간단한 JSON Schema 핵심 규칙(type/required/properties/items)을 검증한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str):
        _assert_schema(payload, _resolve_ref(schema_root, ref), schema_root)
        return

    expected_type = schema.get("type")
    if expected_type == "object":
        assert isinstance(payload, dict)
        payload_obj = cast(dict[str, object], payload)
        required_keys = schema.get("required", [])
        assert isinstance(required_keys, list)
        for key in required_keys:
            assert isinstance(key, str)
            assert key in payload_obj
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for key, sub_schema in properties.items():
            if key not in payload_obj:
                continue
            assert isinstance(sub_schema, dict)
            _assert_schema(payload_obj[key], cast(dict[str, object], sub_schema), schema_root)
        return

    if expected_type == "array":
        assert isinstance(payload, list)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in payload:
                _assert_schema(item, cast(dict[str, object], item_schema), schema_root)
        return

    if expected_type == "string":
        assert isinstance(payload, str)
        return

    if expected_type == "integer":
        assert isinstance(payload, int)
        return

    if expected_type == "boolean":
        assert isinstance(payload, bool)
        return

    if expected_type == "null":
        assert payload is None
        return


def test_cli_doctor_command_outputs_checks(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """doctor 명령은 checks 배열을 포함한 JSON을 출력해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    doctor_schema = cast(dict[str, object], defs["doctor_output"])
    _assert_schema(payload, doctor_schema, schema)


def test_cli_engine_status_outputs_dependencies(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """engine status 명령은 dependencies 정보를 포함해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["engine", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    engine_status_schema = cast(dict[str, object], defs["engine_status_output"])
    _assert_schema(payload, engine_status_schema, schema)


def test_cli_index_output_matches_schema(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """index 명령은 스키마에 정의된 필드를 반환해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["index"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    index_schema = cast(dict[str, object], defs["index_output"])
    _assert_schema(payload, index_schema, schema)


def test_cli_install_print_codemode_matches_schema(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """install --host codex --print 출력이 스키마와 일치해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["install", "--host", "codex", "--print"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    schema = _load_cli_schema()
    defs = cast(dict[str, object], schema["$defs"])
    install_schema = cast(dict[str, object], defs["install_output_codex"])
    _assert_schema(payload, install_schema, schema)


def test_cli_install_apply_updates_gemini_settings_with_backup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """install --host gemini는 설정 파일을 갱신하고 백업을 생성해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    settings_path = gemini_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"httpUrl": "https://example.com"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["install", "--host", "gemini"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["path"] == str(settings_path)
    backup_path = payload["backup_path"]
    assert isinstance(backup_path, str)
    assert Path(backup_path).exists()
    updated = json.loads(settings_path.read_text(encoding="utf-8"))
    mcp_servers = cast(dict[str, object], updated["mcpServers"])
    assert "github" in mcp_servers
    assert "sari" in mcp_servers


def test_cli_install_apply_updates_codex_config_with_section_merge(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """install --host codex는 TOML에서 sari 섹션만 병합해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_dir / "config.toml"
    config_path.write_text(
        "[mcp_servers.github]\ncommand = \"gh\"\n\n[mcp_servers.sari]\ncommand = \"old\"\nargs = [\"x\"]\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli, ["install", "--host", "codex"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["path"] == str(config_path)
    backup_path = payload["backup_path"]
    assert isinstance(backup_path, str)
    assert Path(backup_path).exists()
    content = config_path.read_text(encoding="utf-8")
    assert "[mcp_servers.github]" in content
    assert "[mcp_servers.sari]" in content
    assert "command = \"sari\"" in content
    assert "args = [\"mcp\", \"stdio\"]" in content
    assert "startup_timeout_sec = 45" in content


def test_cli_roots_add_invalid_path_returns_error_contract(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """roots add 실패 시 error.code/message 구조를 반환해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["roots", "add", str(tmp_path / "not-exists")])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "error" in payload
    error = payload["error"]
    assert isinstance(error, dict)
    assert isinstance(error.get("code"), str)
    assert isinstance(error.get("message"), str)


def test_cli_roots_activate_deactivate_updates_workspace_state(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """roots activate/deactivate는 workspace is_active 상태를 갱신해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()

    add_result = runner.invoke(cli, ["roots", "add", str(repo_dir)])
    assert add_result.exit_code == 0

    deactivate_result = runner.invoke(cli, ["roots", "deactivate", str(repo_dir)])
    assert deactivate_result.exit_code == 0
    deactivate_payload = json.loads(deactivate_result.output)
    assert deactivate_payload["workspace"]["is_active"] is False

    activate_result = runner.invoke(cli, ["roots", "activate", str(repo_dir)])
    assert activate_result.exit_code == 0
    activate_payload = json.loads(activate_result.output)
    assert activate_payload["workspace"]["is_active"] is True


def test_cli_pipeline_policy_show_outputs_policy_fields(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline policy show는 정책 필드를 포함한 JSON을 출력해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    result = runner.invoke(cli, ["pipeline", "policy", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "policy" in payload
    policy = payload["policy"]
    assert isinstance(policy, dict)
    assert isinstance(policy.get("deletion_hold"), bool)
    assert isinstance(policy.get("l3_p95_threshold_ms"), int)


def test_cli_pipeline_auto_set_and_status(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline auto set/status 명령은 자동제어 상태를 반영해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()

    set_result = runner.invoke(cli, ["pipeline", "auto", "set", "--enabled", "on"])
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.output)
    assert set_payload["auto_control"]["auto_hold_enabled"] is True

    status_result = runner.invoke(cli, ["pipeline", "auto", "status"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.output)
    assert status_payload["auto_control"]["auto_hold_enabled"] is True


def test_cli_no_args_non_tty_enters_mcp_stdio(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """비대화형 stdin에서 sari 단독 실행은 MCP stdio 경로로 진입해야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    called: dict[str, object] = {}

    class _FakeStdin:
        def isatty(self) -> bool:
            return False

    def _fake_run_stdio_proxy(**kwargs: object) -> int:
        called.update(kwargs)
        return 0

    monkeypatch.setattr("sari.cli.main.sys.stdin", _FakeStdin())
    monkeypatch.setattr("sari.cli.main.run_stdio_proxy", _fake_run_stdio_proxy)

    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "db_path" in called


def test_cli_transport_stdio_option_enters_mcp_stdio(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """--transport stdio 옵션으로 명시 진입할 수 있어야 한다."""
    _prepare_home(tmp_path=tmp_path, monkeypatch=monkeypatch)
    runner = CliRunner()
    called: dict[str, object] = {}

    class _FakeStdin:
        def isatty(self) -> bool:
            return True

    def _fake_run_stdio_proxy(**kwargs: object) -> int:
        called.update(kwargs)
        return 0

    monkeypatch.setattr("sari.cli.main.sys.stdin", _FakeStdin())
    monkeypatch.setattr("sari.cli.main.run_stdio_proxy", _fake_run_stdio_proxy)

    result = runner.invoke(cli, ["--transport", "stdio", "--format", "pack"])

    assert result.exit_code == 0
    assert "db_path" in called
