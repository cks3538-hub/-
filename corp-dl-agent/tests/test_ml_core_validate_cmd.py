"""validate CLI 시험: data_report/split_manifest 생성, --json, 실패 시 보고서 보존과 종료 코드."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from corp_dl_agent.cli import main
from corp_dl_agent.common import read_json
from corp_dl_agent.errors import EXIT_VALIDATION
from corp_dl_agent.ml.split import load_split

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"


def run(args: list[str]) -> int:
    return main(args)


def test_validate_regression_fixture_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_root = tmp_path / "작업 공간"
    code = run(
        [
            "validate",
            "--config",
            str(FIXTURES / "task_regression.yaml"),
            "--set",
            f"paths.data_root={data_root}",
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True and payload["status"] == "PASS" and payload["training_performed"] is False
    assert payload["synthetic"] is True and payload["lock_hash"] == "UNLOCKED"
    assert payload["acceptance"]["state"] == "NEEDS_ACCEPTANCE_CRITERIA"
    out_dir = data_root / "outputs" / "validate" / "clip_insertion_force"
    report = read_json(out_dir / "data_report.json")
    assert report["passed"] and report["encoding_detected"] == "utf-8" and report["n_rows"] == 600
    man = load_split(out_dir / "split_manifest.json")
    assert man.n_groups == {"train": 28, "val": 6, "test": 6} and man.hashes["lock"] == "UNLOCKED"
    assert len(man.hashes["code"]) == 64 and man.hashes["data"] == report["sha256"]
    summary = read_json(out_dir / "validation_summary.json")
    assert (
        summary["checks"]["leakage"]["status"] == "PASS"
        and summary["checks"]["preprocessing"]["fit_on"] == "train_only"
    )
    assert summary["checks"]["split"]["row_fractions"] == man.row_fractions


def test_validate_classification_text_output_and_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"
    code = run(
        [
            "validate",
            "--config",
            str(FIXTURES / "task_classification.yaml"),
            "--set",
            f"paths.data_root={tmp_path}",
            "--output-dir",
            str(out_dir),
            "--lock-hash",
            "abc123",
        ]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert "검증 완료" in text and "NEEDS_ACCEPTANCE_CRITERIA" in text and "utf-8-sig" in text
    man = load_split(out_dir / "split_manifest.json")
    assert man.policy == "time" and man.hashes["lock"] == "abc123"
    assert (out_dir / "data_report.json").is_file() and (out_dir / "validation_summary.json").is_file()


def test_validate_failure_keeps_report_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = yaml.safe_load((FIXTURES / "task_regression.yaml").read_text(encoding="utf-8"))
    lines = (FIXTURES / "clip_regression.csv").read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    t_idx = header.index("insertion_force_n")
    broken = []
    for i, line in enumerate(lines[1:], start=1):
        parts = line.split(",")
        if i in (2, 5):
            parts[t_idx] = ""
        broken.append(",".join(parts))
    (tmp_path / "bad.csv").write_text("\n".join([lines[0], *broken]) + "\n", encoding="utf-8")
    spec["data_path"] = "bad.csv"
    (tmp_path / "task.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    code = run(
        [
            "validate",
            "--config",
            str(tmp_path / "task.yaml"),
            "--set",
            f"paths.data_root={tmp_path / 'ws'}",
            "--json",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_VALIDATION
    payload = json.loads(out)
    assert payload["ok"] is False and payload["error"]["code"] == "E_INPUT_INVALID"
    issues = {i["code"]: i for i in payload["error"]["details"]["issues"]}
    assert issues["MISSING_TARGET"]["count"] == 2 and [
        r["index"] for r in issues["MISSING_TARGET"]["rows"]
    ] == [1, 4]
    report_path = Path(payload["error"]["details"]["data_report_path"])
    assert report_path.is_file() and read_json(report_path)["passed"] is False
    summary = read_json(Path(payload["error"]["details"]["summary_path"]))
    assert summary["status"] == "FAIL" and summary["checks"]["data"]["status"] == "FAIL"
    assert not (report_path.parent / "split_manifest.json").exists()


def test_validate_split_impossible(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = yaml.safe_load((FIXTURES / "task_regression.yaml").read_text(encoding="utf-8"))
    lines = (FIXTURES / "clip_regression.csv").read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    g_idx = header.index("group_id")
    rows = []
    for i, line in enumerate(lines[1:60], start=0):
        parts = line.split(",")
        parts[g_idx] = "G-A" if i % 2 == 0 else "G-B"
        rows.append(",".join(parts))
    (tmp_path / "two_groups.csv").write_text("\n".join([lines[0], *rows]) + "\n", encoding="utf-8")
    spec["data_path"] = "two_groups.csv"
    (tmp_path / "task.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    code = run(
        [
            "validate",
            "--config",
            str(tmp_path / "task.yaml"),
            "--set",
            f"paths.data_root={tmp_path / 'ws'}",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_VALIDATION and payload["error"]["code"] == "E_SPLIT_IMPOSSIBLE"
    summary = read_json(Path(payload["error"]["details"]["summary_path"]))
    assert summary["checks"]["data"]["status"] == "PASS" and summary["checks"]["split"]["status"] == "FAIL"


def test_validate_leakage_detected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = yaml.safe_load((FIXTURES / "task_regression.yaml").read_text(encoding="utf-8"))
    spec["categorical_features"] = ["material_family", "data_source", "row_tag"]
    lines = (FIXTURES / "clip_regression.csv").read_text(encoding="utf-8").splitlines()
    rows = [lines[0] + ",row_tag"] + [f"{line},tag-{i:04d}" for i, line in enumerate(lines[1:])]
    (tmp_path / "leak.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    spec["data_path"] = "leak.csv"
    (tmp_path / "task.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    code = run(
        [
            "validate",
            "--config",
            str(tmp_path / "task.yaml"),
            "--set",
            f"paths.data_root={tmp_path / 'ws'}",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_VALIDATION and payload["error"]["code"] == "E_LEAKAGE"
    assert payload["error"]["details"]["findings"][0]["column"] == "row_tag"


def test_validate_help_is_korean(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        run(["validate", "--help"])
    assert ei.value.code == 0
    out = " ".join(capsys.readouterr().out.split())  # argparse 줄바꿈 정규화
    assert (
        "TaskSpec" in out
        and "학습은 하지 않습니다" in out
        and "--lock-hash" in out
        and "data_report.json" in out
    )


def test_validate_invalid_taskspec_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "bad.yaml").write_text("task_id: x\nextra: 1\n", encoding="utf-8")
    code = run(["validate", "--config", str(tmp_path / "bad.yaml"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_VALIDATION and payload["error"]["code"] == "E_SCHEMA_INVALID"
