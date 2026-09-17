from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from corp_dl_agent.cli import COMMAND_MODULES, build_parser, main

ROOT = Path(__file__).resolve().parents[1]

# 최종 통합 시점에는 빈 목록이어야 한다. (구현 전 단계에서만 허용되는 모듈 목록)
ALLOWED_MISSING_BEFORE_INTEGRATION = {
    "corp_dl_agent.commands.demo_cmd",  # demo 는 다음 단계(통합 시나리오)에서 구현
}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "corp_dl_agent", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        encoding="utf-8",
    )


def test_help_and_version() -> None:
    r = run_cli("--help")
    assert r.returncode == 0 and "설계·문서 업무 자동화" in r.stdout
    r = run_cli("version", "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["version"]


def test_all_command_modules_registered() -> None:
    parser = build_parser()
    missing = set(parser.get_default("_missing_command_modules") or [])
    assert missing <= ALLOWED_MISSING_BEFORE_INTEGRATION, f"허용되지 않은 누락 명령 모듈: {missing}"
    assert len(COMMAND_MODULES) == 15


def test_unknown_command_korean_usage_error() -> None:
    r = run_cli("없는명령")
    assert r.returncode == 2 and "E_USAGE" in r.stderr and "--help" in r.stderr


def test_no_command_prints_help() -> None:
    assert main([]) == 2


def test_doctor_json_masks_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    r = run_cli(
        "doctor",
        "--json",
        "--no-paths",
        "--profile",
        "corp-gateway",
        "--set",
        "gateway.enabled=true",
        "--set",
        "gateway.base_url=https://llm.example.internal",
        "--set",
        "gateway.model_id=m1",
        "--set",
        "gateway.secret_ref=env:SECRET_TOKEN_X",
        "--set",
        "gateway.approved_origins=[https://llm.example.internal]",
    )
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["gateway"]["secret_ref"] == "***"
    assert "SECRET_TOKEN_X" not in r.stdout
    assert d["host"]["python_abi"].startswith("cp")
    assert d["summary"]["core"] == "PASS"


def test_doctor_export_preview_has_only_allowed_fields(tmp_path: Path) -> None:
    r = run_cli("doctor", "--export-diagnostics-preview", "--set", f"paths.data_root={tmp_path}")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert set(d["host"]) <= {
        "os",
        "os_release",
        "architecture",
        "python_implementation",
        "python_version",
        "python_abi",
        "in_virtualenv",
    }
    assert "python_executable" not in json.dumps(d)
    assert str(tmp_path) not in r.stdout


def test_selftest_json(tmp_path: Path) -> None:
    r = run_cli(
        "self-test",
        "--json",
        "--set",
        f"paths.data_root={tmp_path}",
        "--output",
        str(tmp_path / "selftest.json"),
    )
    d = json.loads(r.stdout)
    assert r.returncode == d["exit_code"]
    names = {i["name"]: i for i in d["items"]}
    assert names["units_1kg"]["status"] == "PASS"
    assert names["sqlite"]["status"] == "PASS"
    assert names["atomic_write_korean_path"]["status"] == "PASS"
    assert names["torch_cpu"]["status"] == "PASS"
    assert (tmp_path / "selftest.json").is_file()


def test_config_validate_ok_and_errors(tmp_path: Path) -> None:
    good = tmp_path / "설정 폴더" / "company_config.yaml"
    good.parent.mkdir(parents=True)
    good.write_text("profile: corp-offline\npaths:\n  data_root: ../ws\n", encoding="utf-8")
    r = run_cli("config", "validate", "--config", str(good))
    assert r.returncode == 0 and "통과" in r.stdout
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile: corp-offline\nunknown_key: 1\n", encoding="utf-8")
    r = run_cli("config", "validate", "--config", str(bad))
    assert r.returncode == 3 and "E_CONFIG_UNKNOWN_KEY" in r.stderr and "unknown_key" in r.stderr
    bad2 = tmp_path / "bad2.yaml"
    bad2.write_text("profile: corp-offline\nml:\n  num_workers: abc\n", encoding="utf-8")
    r = run_cli("config", "validate", "--config", str(bad2))
    assert r.returncode == 3 and "ml.num_workers" in r.stderr
    r = run_cli("config", "validate")
    assert r.returncode == 2


def test_config_show_masks(tmp_path: Path) -> None:
    r = run_cli("config", "show", "--set", "integrations.jira.secret_ref=env:JIRA_T")
    assert r.returncode == 0 and "JIRA_T" not in r.stdout and '"***"' in r.stdout


def test_menu_list_and_choice(tmp_path: Path) -> None:
    r = run_cli("menu", "--list")
    assert r.returncode == 0 and "설정 검사" in r.stdout and "0. 종료" in r.stdout
    r = run_cli("menu", "--choice", "6", "--set", f"paths.data_root={tmp_path}")
    assert "self-test" in r.stdout
    r = run_cli("menu", "--choice", "99")
    assert r.returncode == 2 and "E_USAGE" in r.stdout
