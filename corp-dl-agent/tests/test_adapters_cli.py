"""integrations 명령 시험: doctor(연결 없음, 마스킹) / preview(네트워크 없음, write off)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_adapters_mockserver import MockServer

from corp_dl_agent.cli import main
from corp_dl_agent.common import read_json
from corp_dl_agent.errors import EXIT_USAGE, EXIT_VALIDATION

TOKEN_ENV = "CORP_DL_AGENT_TEST_CLI_TOKEN"


def _common(tmp_path: Path, *roots: Path) -> list[str]:
    args = ["--set", f"paths.data_root={tmp_path / 'ws'}"]
    if roots:
        args += ["--set", "paths.input_roots=[" + ",".join(str(r) for r in roots) + "]"]
    return args


def test_integrations_doctor_offline_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["integrations", "doctor", "--json", *_common(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["profile"] == "corp-offline" and out["network_allowed"] is False
    assert out["gateway"]["status"] == "NOT_RUN"
    assert {k: v["status"] for k, v in out["atlassian"]["products"].items()} == {
        "bitbucket": "NOT_RUN",
        "jira": "NOT_RUN",
        "confluence": "NOT_RUN",
        "bamboo": "NOT_RUN",
    }
    assert out["atlassian"]["write_enabled"] is False and out["live_verified"] is False
    assert out["outbound_guard"]["active"] is True and out["outbound_guard"]["os_level_isolation"] is False
    assert out["cad_adapters"]["catia_v5_com"]["available"] is False


def test_integrations_doctor_text_masks_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "cli-doctor-secret-value-000111"
    monkeypatch.setenv(TOKEN_ENV, secret)
    with MockServer() as srv:
        rc = main(
            [
                "integrations",
                "doctor",
                *_common(tmp_path),
                "--set",
                "profile=corp-gateway",
                "--set",
                "gateway.enabled=true",
                "--set",
                f"gateway.base_url={srv.base_url}",
                "--set",
                "gateway.model_id=synthetic-model-id",
                "--set",
                f"gateway.secret_ref=env:{TOKEN_ENV}",
                "--set",
                f"gateway.approved_origins=[{srv.base_url}]",
                "--set",
                "integrations.jira.enabled=true",
                "--set",
                f"integrations.jira.base_url={srv.base_url}",
                "--set",
                f"integrations.jira.secret_ref=env:{TOKEN_ENV}",
                "--set",
                f"integrations.approved_origins=[{srv.base_url}]",
            ]
        )
        text = capsys.readouterr().out
        assert rc == 0 and srv.requests == []  # doctor 는 기본적으로 연결하지 않는다
    assert "jira: NOT_RUN" in text and "live verified: 아니오" in text
    assert secret not in text and "bamboo: NOT_RUN" in text


def test_integrations_preview_jira(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = tmp_path / "in" / "issue.json"
    payload.parent.mkdir(parents=True)
    payload.write_text(
        json.dumps({"summary": "합성 이슈", "description": "d", "project_key": "SYN"}, ensure_ascii=False),
        encoding="utf-8",
    )
    out = tmp_path / "out" / "preview.json"
    rc = main(
        [
            "integrations",
            "preview",
            "--product",
            "jira",
            "--payload",
            str(payload),
            "--output",
            str(out),
            "--json",
            *_common(tmp_path, tmp_path),
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["performed"] is False and result["write_enabled"] is False
    assert (
        result["preview"]["action"] == "create_issue"
        and result["preview"]["body"]["fields"]["summary"] == "합성 이슈"
    )
    assert out.is_file() and read_json(out)["would_write"] is True


def test_integrations_preview_text_output_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "pr.json"
    payload.write_text(
        json.dumps(
            {
                "title": "t",
                "source_branch": "feat",
                "target_branch": "main",
                "project": "SYN",
                "repo_slug": "core",
            }
        ),
        encoding="utf-8",
    )
    rc = main(
        [
            "integrations",
            "preview",
            "--product",
            "bitbucket",
            "--payload",
            str(payload),
            *_common(tmp_path, tmp_path),
        ]
    )
    text = capsys.readouterr().out
    assert rc == 0 and "원격 쓰기 수행 안 함" in text and "pull-requests" in text
    # payload 없음 -> E_INPUT_INVALID
    rc2 = main(
        [
            "integrations",
            "preview",
            "--product",
            "jira",
            "--payload",
            str(tmp_path / "missing.json"),
            *_common(tmp_path, tmp_path),
        ]
    )
    assert rc2 == EXIT_VALIDATION
    # 잘못된 payload (summary 없음)
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    rc3 = main(
        [
            "integrations",
            "preview",
            "--product",
            "jira",
            "--payload",
            str(bad),
            "--json",
            *_common(tmp_path, tmp_path),
        ]
    )
    assert (
        rc3 == EXIT_VALIDATION and json.loads(capsys.readouterr().out)["error"]["code"] == "E_INPUT_INVALID"
    )
    # 승인 root 밖 payload
    outside = tmp_path.parent / "outside_payload.json"
    outside.write_text('{"summary": "x"}', encoding="utf-8")
    try:
        rc4 = main(
            [
                "integrations",
                "preview",
                "--product",
                "jira",
                "--payload",
                str(outside),
                *_common(tmp_path, tmp_path),
            ]
        )
        assert rc4 == EXIT_VALIDATION
    finally:
        outside.unlink()
    # 알 수 없는 제품 -> argparse 사용법 오류
    with pytest.raises(SystemExit) as se:
        main(["integrations", "preview", "--product", "trello", "--payload", str(payload)])
    assert se.value.code == EXIT_USAGE


def test_integrations_without_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["integrations"])
    assert rc == 2 and "doctor" in capsys.readouterr().out
