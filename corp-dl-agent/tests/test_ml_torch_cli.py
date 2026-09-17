"""CLI 시험: run --fast / status / pause / resume / cancel / report / predict (한국어 help, --json, 종료 코드)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_ml_torch_common import (
    FIXTURES,
    budget,
    cap_torch_threads,
    guarded,
    make_cfg,
    small_task,
    workspace,
)

from corp_dl_agent.cli import main
from corp_dl_agent.errors import EXIT_RUNTIME, EXIT_VALIDATION
from corp_dl_agent.ml.runner import run_task
from corp_dl_agent.ml.trainer import HookContext, TrainerHooks

pytestmark = pytest.mark.torch


@pytest.fixture(autouse=True)
def _guard() -> Iterator[None]:
    cap_torch_threads()
    with guarded():
        yield


def _out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def test_run_fast_status_report_predict_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_root = tmp_path / "작업 공간"
    common = ["--set", f"paths.data_root={data_root}"]
    code = main(
        [
            "run",
            "--config",
            str(FIXTURES / "task_regression.yaml"),
            *common,
            "--run-id",
            "cli-1",
            "--fast",
            "--json",
        ]
    )
    assert code == 0
    payload = _out(capsys)
    assert payload["ok"] is True and payload["status"] == "COMPLETED" and payload["synthetic"] is True
    assert (
        len([c for c in payload["candidates"] if c["kind"] == "mlp"]) == 1
        and payload["acceptance"]["state"] == "NEEDS_ACCEPTANCE_CRITERIA"
    )
    run_dir = Path(payload["run_dir"])
    assert (run_dir / "final_evaluation.json").is_file() and (run_dir / "export" / "manifest.json").is_file()

    assert main(["status", "--run-id", "cli-1", *common, "--json"]) == 0
    st = _out(capsys)
    assert (
        st["run"]["status"] == "COMPLETED"
        and len(st["trials"]) == 4
        and st["summary"]["selected"] == payload["selected"]
    )
    assert main(["status", "--run-id", "cli-1", *common]) == 0
    text = capsys.readouterr().out
    assert "COMPLETED" in text and "선택:" in text and "test 1회" in text

    assert main(["report", "--run-id", "cli-1", *common, "--json"]) == 0
    rep = _out(capsys)
    assert Path(rep["report_md"]).is_file()
    assert rep["html_status"] == "PASS" and Path(rep["report_html"]).is_file()
    html = Path(rep["report_html"]).read_text(encoding="utf-8")
    assert "합성" in html and "http://" not in html and "https://" not in html  # 외부 CDN 없음

    csv_out = data_root / "outputs" / "pred.csv"
    code = main(
        [
            "predict",
            "--model",
            str(run_dir / "export"),
            "--input",
            str(FIXTURES / "clip_regression.csv"),
            "--output",
            str(csv_out),
            *common,
            "--json",
        ]
    )
    assert code == EXIT_VALIDATION
    err = _out(capsys)
    assert err["ok"] is False and err["error"]["code"] == "E_ARTIFACT_SYNTHETIC"
    code = main(
        [
            "predict",
            "--model",
            str(run_dir / "export"),
            "--input",
            str(FIXTURES / "clip_regression.csv"),
            "--output",
            str(csv_out),
            *common,
            "--allow-synthetic",
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "예측 완료: 600행" in text and "합성" in text and csv_out.is_file()

    # 같은 run_id 로 다시 run → 오류 (resume 안내)
    code = main(
        [
            "run",
            "--config",
            str(FIXTURES / "task_regression.yaml"),
            *common,
            "--run-id",
            "cli-1",
            "--fast",
            "--json",
        ]
    )
    assert code == EXIT_VALIDATION and _out(capsys)["error"]["code"] == "E_INPUT_INVALID"
    # 완료 run 의 resume 은 재평가 없이 요약을 돌려준다
    assert main(["resume", "--run-id", "cli-1", *common, "--fast", "--json"]) == 0
    assert _out(capsys)["test_locked"] is True


class PauseOnce(TrainerHooks):
    def __init__(self) -> None:
        self.fired = False

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:
        if not self.fired:
            self.fired = True
            ctx.coordinator.request_pause(ctx.run_id)


def test_pause_resume_cancel_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    common = ["--set", f"paths.data_root={cfg.paths.data_root}"]
    spec = small_task(tmp_path, "regression")
    fast = {
        "wall_time_seconds": 120,
        "max_candidates": 1,
        "max_epochs": 2,
        "patience": 2,
        "max_calls": 0,
        "max_tokens": 0,
        "mode": "custom",
    }
    s = run_task(spec, cfg, ws, run_id="cli-p", budget_override=fast, hooks=PauseOnce())
    assert s.status == "PAUSED"
    assert main(["status", "--run-id", "cli-p", *common, "--json"]) == 0
    st = _out(capsys)
    assert st["run"]["status"] == "PAUSED" and st["lease_valid"] is False
    assert main(["pause", "--run-id", "cli-p", *common]) == 0
    assert "pause 요청" in capsys.readouterr().out
    assert main(["resume", "--run-id", "cli-p", *common, "--fast", "--json"]) == 0
    res = _out(capsys)
    assert res["status"] == "COMPLETED" and res["resumed"] is True
    mlp = next(c for c in res["candidates"] if c["kind"] == "mlp")
    assert mlp["epochs"] == 2 and mlp["attempts"] == 2

    # 같은 fingerprint 의 완료 trial 은 재사용(SKIPPED)되어 epoch hook 이 실행되지 않으므로 다른 task_id 로 새 실험을 만든다
    spec_c = small_task(tmp_path, "regression", task_id="small_regression_cancel")
    s2 = run_task(spec_c, cfg, ws, run_id="cli-c", budget_override=fast, hooks=PauseOnce())
    assert s2.status == "PAUSED"
    assert main(["cancel", "--run-id", "cli-c", *common, "--json"]) == 0
    can = _out(capsys)
    assert can["status"] == "CANCELLED" and can["immediate"] is True
    code = main(["resume", "--run-id", "cli-c", *common, "--fast", "--json"])
    assert code == EXIT_RUNTIME and _out(capsys)["error"]["code"] == "E_STATE_TRANSITION"
    code = main(["status", "--run-id", "없는run", *common, "--json"])
    assert code == EXIT_VALIDATION and _out(capsys)["error"]["code"] == "E_INPUT_INVALID"
    assert main(["resume", "--run-id", "없는run", *common, "--json"]) == EXIT_VALIDATION


def test_help_is_korean(capsys: pytest.CaptureFixture[str]) -> None:
    for argv, needle in (
        (["run", "--help"], "TaskSpec"),
        (["predict", "--help"], "synthetic"),
        (["resume", "--help"], "E_FINGERPRINT_CHANGED"),
        (["status", "--help"], "run 식별자"),
    ):
        with pytest.raises(SystemExit) as ei:
            main(argv)
        assert ei.value.code == 0
        out = " ".join(capsys.readouterr().out.split())
        assert needle in out
        if argv[0] in ("resume", "status"):
            assert "--run-id" in out
    with pytest.raises(SystemExit) as ei2:
        main(["run"])  # --config 필수
    assert ei2.value.code == 2
    assert "E_USAGE" in capsys.readouterr().err


def test_fast_budget_override_matches_config_hash_for_resume(tmp_path: Path) -> None:
    """--fast 로 시작한 run 은 --fast 로만 재개된다 (config hash)."""
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    fast = {
        "wall_time_seconds": 120,
        "max_candidates": 1,
        "max_epochs": 2,
        "patience": 2,
        "max_calls": 0,
        "max_tokens": 0,
        "mode": "custom",
    }
    s = run_task(spec, cfg, ws, run_id="fast-1", budget_override=fast, hooks=PauseOnce())
    assert s.status == "PAUSED"
    from corp_dl_agent.errors import AgentError

    with pytest.raises(AgentError) as ei:
        run_task(spec, cfg, ws, run_id="fast-1", resume=True, budget_override=budget(epochs=3, cands=1))
    assert ei.value.code == "E_FINGERPRINT_CHANGED"
    assert run_task(spec, cfg, ws, run_id="fast-1", resume=True, budget_override=fast).status == "COMPLETED"
