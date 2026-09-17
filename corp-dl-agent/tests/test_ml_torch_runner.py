"""runner 시험 (BUILD_SPEC [16] B/C): 회귀/분류 run 완료·산출물, test 잠금, 완료 trial skip, resume 거부(config/lock),
acceptance 상태, 예산 초과 → BUDGET_EXCEEDED, 강제 종료 후 다른 worker 재개, 데이터 오류 → FAILED, cancel."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.common import read_json, sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.evaluation import is_finite_metrics
from corp_dl_agent.ml.export import load_bundle, predict
from corp_dl_agent.ml.runner import FINAL_EVAL_NAME, load_summary, run_task
from corp_dl_agent.ml.taskspec import load_taskspec
from corp_dl_agent.ml.trainer import HookContext, SimulatedCrash, TrainerHooks
from corp_dl_agent.state.coordinator import Coordinator
from corp_dl_agent.state.db import StateDB
from corp_dl_agent.state.machine import RunStatus, TrialStatus
from test_ml_torch_common import FIXTURES, FakeClock, budget, make_cfg, read_jsonl, small_task, tracker, workspace

pytestmark = pytest.mark.torch

RUN_FILES = (
    "taskspec.json",
    "run_config.json",
    "environment.json",
    "manifest.json",
    "data_report.json",
    "split_manifest.json",
    "plan.json",
    "events.jsonl",
    "epoch_metrics.jsonl",
    "metrics.csv",
    "final_evaluation.json",
    "model_card.json",
    "model_card.md",
    "report_ko.md",
    "summary.json",
    "export/manifest.json",
)


def _db_status(ws: Any, run_id: str) -> RunStatus:
    db = StateDB(ws.state_db)
    try:
        return Coordinator(db, "reader").get_run(run_id).status
    finally:
        db.close()


def _trials(ws: Any, run_id: str) -> list[Any]:
    db = StateDB(ws.state_db)
    try:
        return Coordinator(db, "reader").list_trials(run_id)
    finally:
        db.close()


def test_regression_run_on_fixture_completes_with_all_artifacts(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = load_taskspec(FIXTURES / "task_regression.yaml")
    s = run_task(spec, cfg, ws, run_id="reg-1", budget_override=budget(epochs=2, cands=2))
    assert s.status == "COMPLETED" and s.synthetic and s.data_origin == "synthetic"
    run_dir = ws.run_dir("reg-1")
    for name in RUN_FILES:
        assert (run_dir / name).is_file(), name
    assert [c.name for c in s.candidates][:3] == ["DummyRegressor", "Ridge", "HistGradientBoostingRegressor"]
    assert [c.kind for c in s.candidates] == ["sklearn"] * 3 + ["mlp"] * 2
    assert all(c.status == "COMPLETED" for c in s.candidates) and all(c.epochs == 2 for c in s.candidates if c.kind == "mlp")
    assert is_finite_metrics({k: s.final_evaluation[k] for k in ("mae", "rmse", "r2", "p95_abs_error")})
    assert s.final_evaluation["unit"] == "N" and s.selected in {c.name for c in s.candidates}
    assert s.acceptance["state"] == "NEEDS_ACCEPTANCE_CRITERIA"
    fe = read_json(run_dir / FINAL_EVAL_NAME)
    assert fe["locked"] is True and fe["selected"] == s.selected and fe["n_test"] == s.split["n_rows"]["test"]
    card = read_json(run_dir / "model_card.json")
    assert card["data_origin"] == "synthetic" and card["production_auto_select"] is False
    env = read_json(run_dir / "environment.json")
    assert env["device"] == "cpu" and env["num_workers"] == 0 and env["seed"] == 42 and "torch" in env["versions"]
    assert "hostname" not in json.dumps(env)
    plan = read_json(run_dir / "plan.json")
    assert len(plan["mlp_candidates"]) == 2 and plan["max_epochs"] == 2
    events = [e["event"] for e in read_jsonl(run_dir / "events.jsonl")]
    assert "run_completed" in events and "final_evaluation_written" in events and events.count("epoch_completed") == 4
    assert _db_status(ws, "reg-1") is RunStatus.COMPLETED
    assert all(t.status is TrialStatus.COMPLETED for t in _trials(ws, "reg-1"))
    bundle = load_bundle(run_dir / "export", allow_synthetic=True)
    assert bundle.manifest.run_id == "reg-1" and bundle.manifest.model_name == s.selected
    report = (run_dir / "report_ko.md").read_text(encoding="utf-8")
    assert "합성" in report and s.selected in report
    manifest = read_json(run_dir / "manifest.json")
    assert "final_evaluation.json" in manifest["files"] and manifest["files"]["final_evaluation.json"]["sha256"] == sha256_file(run_dir / FINAL_EVAL_NAME)
    metrics_csv = (run_dir / "metrics.csv").read_text(encoding="utf-8").splitlines()
    assert metrics_csv[0].startswith("name,kind,trial_id,status") and len(metrics_csv) == 6


def test_classification_run_selects_threshold_and_exports(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "binary_classification")
    s = run_task(spec, cfg, ws, run_id="cls-1", budget_override=budget(epochs=2, cands=1))
    assert s.status == "COMPLETED" and s.threshold is not None and 0.0 <= s.threshold <= 1.0
    fe = s.final_evaluation
    assert is_finite_metrics({k: fe[k] for k in ("average_precision", "roc_auc", "f1", "precision", "recall")})
    assert set(fe["confusion_matrix"]) == {"tn", "fp", "fn", "tp"} and fe["threshold"] == s.threshold
    bundle = load_bundle(ws.run_dir("cls-1") / "export", allow_synthetic=True)
    assert bundle.manifest.threshold == s.threshold
    import pandas as pd

    df = pd.read_csv(spec.data_path, dtype=str, keep_default_na=False)
    out = predict(bundle, df.head(10))
    assert {"probability", "label"} <= set(out.columns) and len(out) == 10


def test_final_evaluation_locked_on_resume_of_completed_run(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    s1 = run_task(spec, cfg, ws, run_id="lock-1", budget_override=budget(epochs=2, cands=1))
    fe_path = ws.run_dir("lock-1") / FINAL_EVAL_NAME
    before = sha256_file(fe_path)
    export_before = sha256_file(ws.run_dir("lock-1") / "export" / "manifest.json")
    s2 = run_task(spec, cfg, ws, run_id="lock-1", resume=True, budget_override=budget(epochs=2, cands=1))
    assert s2.status == "COMPLETED" and s2.test_locked is True and s2.final_evaluation == s1.final_evaluation
    assert sha256_file(fe_path) == before and sha256_file(ws.run_dir("lock-1") / "export" / "manifest.json") == export_before
    events = [e["event"] for e in read_jsonl(ws.run_dir("lock-1") / "events.jsonl")]
    assert events.count("final_evaluation_written") == 1 and "resume_noop_completed" in events
    with pytest.raises(AgentError) as ei:  # 같은 run_id 로 새로 시작할 수 없다
        run_task(spec, cfg, ws, run_id="lock-1", budget_override=budget(epochs=2, cands=1))
    assert ei.value.code == "E_INPUT_INVALID" and "resume" in (ei.value.hint or "")


def test_completed_trials_are_skipped_in_a_new_run(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    s1 = run_task(spec, cfg, ws, run_id="skip-1", budget_override=budget(epochs=2, cands=1))
    t0 = time.monotonic()
    s2 = run_task(spec, cfg, ws, run_id="skip-2", budget_override=budget(epochs=2, cands=1))
    assert s2.status == "COMPLETED" and time.monotonic() - t0 < 30
    assert all(c.status == "SKIPPED" for c in s2.candidates) and len(s2.trials_skipped) == 4
    assert [c.reused_from for c in s2.candidates] == [c.trial_id for c in s1.candidates]
    for c1, c2 in zip(s1.candidates, s2.candidates, strict=True):
        assert c1.metrics.get(spec.metric) == c2.metrics.get(spec.metric)
    assert s2.selected == s1.selected and s2.final_evaluation == s1.final_evaluation
    assert not (ws.run_dir("skip-2") / "epoch_metrics.jsonl").exists()  # 학습을 하지 않았다
    trials = {t.trial_id: t for t in _trials(ws, "skip-2")}
    assert all(t.status is TrialStatus.SKIPPED and t.reason.startswith("reused:") for t in trials.values())
    assert load_bundle(ws.run_dir("skip-2") / "export", allow_synthetic=True).manifest.model_name == s1.selected
    # 산출물이 훼손된 완료 trial 은 재사용하지 않는다 (다시 학습)
    sel = next(c for c in s1.candidates if c.kind == "mlp")
    best = ws.run_dir("skip-1") / "checkpoints" / sel.trial_id / "best.pt"
    best.write_bytes(best.read_bytes() + b"\x00")
    s3 = run_task(spec, cfg, ws, run_id="skip-3", budget_override=budget(epochs=2, cands=1))
    statuses = {c.name: c.status for c in s3.candidates}
    assert statuses[sel.name] == "COMPLETED" and all(v == "SKIPPED" for k, v in statuses.items() if k != sel.name)


class PauseAtFirstEpoch(TrainerHooks):
    def __init__(self, action: str = "pause") -> None:
        self.action = action
        self.fired = False

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:
        if not self.fired and epoch == 0:
            self.fired = True
            if self.action == "pause":
                ctx.coordinator.request_pause(ctx.run_id)
            elif self.action == "cancel":
                ctx.coordinator.request_cancel(ctx.run_id)
            elif self.action == "crash":
                raise SimulatedCrash()


def test_pause_then_resume_rejects_changed_config_or_lock(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    b = budget(epochs=3, cands=1)
    s = run_task(spec, cfg, ws, run_id="pr-1", budget_override=b, hooks=PauseAtFirstEpoch())
    assert s.status == "PAUSED" and _db_status(ws, "pr-1") is RunStatus.PAUSED
    mlp = next(c for c in s.candidates if c.kind == "mlp")
    assert mlp.status == "PAUSED" and mlp.epochs == 1
    assert "resume" in s.message
    with pytest.raises(AgentError) as ei:
        run_task(spec, cfg, ws, run_id="pr-1", resume=True, budget_override=budget(epochs=5, cands=1))
    assert ei.value.code == "E_FINGERPRINT_CHANGED"
    with pytest.raises(AgentError) as ei2:
        run_task(spec, make_cfg(tmp_path, **{"ml.device": "cuda"}), ws, run_id="pr-1", resume=True, budget_override=b)
    assert ei2.value.code == "E_FINGERPRINT_CHANGED"
    with pytest.raises(AgentError) as ei3:
        run_task(spec, cfg, ws, run_id="pr-1", resume=True, budget_override=b, lock_hash="lock-B")
    assert ei3.value.code == "E_FINGERPRINT_CHANGED"
    assert _db_status(ws, "pr-1") is RunStatus.PAUSED  # 거부는 상태를 바꾸지 않는다
    s2 = run_task(spec, cfg, ws, run_id="pr-1", resume=True, budget_override=b)
    assert s2.status == "COMPLETED" and s2.resumed is True
    mlp2 = next(c for c in s2.candidates if c.kind == "mlp")
    assert mlp2.epochs == 3 and mlp2.attempts == 2
    rows = read_jsonl(ws.run_dir("pr-1") / "epoch_metrics.jsonl")
    assert [r["epoch"] for r in rows] == [0, 1, 2]
    assert [c.status for c in s2.candidates if c.kind == "sklearn"] == ["COMPLETED"] * 3  # 기준 모델은 재사용
    with pytest.raises(AgentError) as ei4:  # resume 인데 run_id 없음
        run_task(spec, cfg, ws, resume=True)
    assert ei4.value.code == "E_USAGE"
    with pytest.raises(AgentError) as ei5:
        run_task(spec, cfg, ws, run_id="no-such-run", resume=True)
    assert ei5.value.code == "E_INPUT_INVALID"


def test_forced_kill_then_resume_by_other_worker_after_lease_expiry(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    b = budget(epochs=3, cands=1)
    with pytest.raises(SimulatedCrash):
        run_task(spec, cfg, ws, run_id="kill-1", budget_override=b, hooks=PauseAtFirstEpoch("crash"), worker_id="worker-A", lease_ttl_seconds=0.5)
    assert _db_status(ws, "kill-1") is RunStatus.RUNNING
    with pytest.raises(AgentError) as ei:
        run_task(spec, cfg, ws, run_id="kill-1", resume=True, budget_override=b, worker_id="worker-B")
    assert ei.value.code == "E_LEASE_HELD"
    time.sleep(0.6)
    s = run_task(spec, cfg, ws, run_id="kill-1", resume=True, budget_override=b, worker_id="worker-B")
    assert s.status == "COMPLETED"
    mlp = next(c for c in s.candidates if c.kind == "mlp")
    assert mlp.epochs == 3 and mlp.attempts == 2
    events = [e["event"] for e in read_jsonl(ws.run_dir("kill-1") / "events.jsonl")]
    assert "resumed_from_checkpoint" in events and "run_resumed" in events
    db = StateDB(ws.state_db)
    try:
        db_events = [e["event"] for e in Coordinator(db, "reader").list_events("kill-1")]
    finally:
        db.close()
    assert "lease_takeover" in db_events


class AdvanceClock(TrainerHooks):
    def __init__(self, clock: FakeClock, seconds: float) -> None:
        self.clock = clock
        self.seconds = seconds

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:
        self.clock.advance(self.seconds)


def test_budget_exceeded_then_resume_with_new_budget(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    clock = FakeClock()
    s = run_task(spec, cfg, ws, run_id="bud-1", budget_override=tracker(epochs=3, wall=100, clock=clock), hooks=AdvanceClock(clock, 200.0))
    assert s.status == "BUDGET_EXCEEDED" and _db_status(ws, "bud-1") is RunStatus.BUDGET_EXCEEDED
    assert s.budget["exceeded"] and s.budget["guarantee"] == "none" and "완료 보증" in s.message
    s2 = run_task(spec, cfg, ws, run_id="bud-1", resume=True, budget_override=budget(epochs=3, cands=1, wall=300))
    assert s2.status == "COMPLETED"
    assert next(c for c in s2.candidates if c.kind == "mlp").epochs == 3
    # 기준 모델 단계에서 이미 초과한 경우도 BUDGET_EXCEEDED 로 안전 종료한다
    clock2 = FakeClock(t=1000.0)
    t2 = tracker(epochs=3, wall=10, clock=clock2)
    clock2.advance(20.0)
    s3 = run_task(spec, cfg, ws, run_id="bud-2", budget_override=t2)
    assert s3.status == "BUDGET_EXCEEDED" and _db_status(ws, "bud-2") is RunStatus.BUDGET_EXCEEDED


def test_cancel_request_stops_run(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    s = run_task(spec, cfg, ws, run_id="can-1", budget_override=budget(epochs=3, cands=1), hooks=PauseAtFirstEpoch("cancel"))
    assert s.status == "CANCELLED" and _db_status(ws, "can-1") is RunStatus.CANCELLED
    with pytest.raises(AgentError) as ei:
        run_task(spec, cfg, ws, run_id="can-1", resume=True, budget_override=budget(epochs=3, cands=1))
    assert ei.value.code == "E_STATE_TRANSITION"


def test_acceptance_defined_pass_and_fail(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec_pass = small_task(tmp_path, "regression", acceptance={"metric": "mae", "threshold": 1e6, "direction": "min"}, task_id="acc_pass")
    s = run_task(spec_pass, cfg, ws, run_id="acc-1", budget_override=budget(epochs=2, cands=1))
    assert s.status == "COMPLETED" and s.acceptance["state"] == "PASS" and s.acceptance["observed"] == s.final_evaluation["mae"]
    spec_fail = small_task(tmp_path, "regression", acceptance={"metric": "mae", "threshold": 0.0, "direction": "min"}, task_id="acc_fail")
    s2 = run_task(spec_fail, cfg, ws, run_id="acc-2", budget_override=budget(epochs=2, cands=1))
    assert s2.status == "COMPLETED" and s2.acceptance["state"] == "FAIL"
    card = read_json(ws.run_dir("acc-2") / "model_card.json")
    assert card["acceptance"]["state"] == "FAIL"


def test_data_error_marks_run_failed(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    ws = workspace(cfg)
    spec = small_task(tmp_path, "regression")
    lines = Path(spec.data_path).read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    t_idx = header.index(spec.target)
    parts = lines[3].split(",")
    parts[t_idx] = ""
    lines[3] = ",".join(parts)
    Path(spec.data_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        run_task(spec, cfg, ws, run_id="bad-1", budget_override=budget(epochs=2, cands=1))
    assert ei.value.code == "E_INPUT_INVALID"
    assert _db_status(ws, "bad-1") is RunStatus.FAILED
    summary = load_summary(ws.run_dir("bad-1"))
    assert summary is not None and summary.status == "FAILED" and "E_INPUT_INVALID" in summary.message
    assert (ws.run_dir("bad-1") / "data_report.json").is_file()
