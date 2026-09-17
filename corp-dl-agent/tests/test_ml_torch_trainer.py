"""trainer 시험 (BUILD_SPEC [16] B/C): 완료·finite metrics, OOM 모의 주입(attempt 2회), NaN 주입(LR 교정 1회),
pause/resume(epoch 수), 강제 종료 후 resume, checkpoint 손상 시 best 로 후퇴, 예산 초과, cancel, fingerprint 변경 거부."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_ml_torch_common import (
    FakeClock,
    cap_torch_threads,
    guarded,
    make_cfg,
    prepared_data,
    read_jsonl,
    running_coordinator,
    small_task,
    tracker,
)

from corp_dl_agent.errors import AgentError
from corp_dl_agent.logs import EventLog
from corp_dl_agent.ml.checkpoint import BEST_NAME, LATEST_NAME
from corp_dl_agent.ml.evaluation import is_finite_metrics
from corp_dl_agent.ml.registry import demo_candidates
from corp_dl_agent.ml.trainer import (
    HookContext,
    SimulatedCrash,
    SimulatedOOM,
    TrainerHooks,
    _batch_layout,
    train_candidate,
)
from corp_dl_agent.state.machine import AttemptStatus, TrialStatus

pytestmark = pytest.mark.torch
RUN_ID = "run-t"


@pytest.fixture(autouse=True)
def _guard() -> Iterator[None]:
    cap_torch_threads()
    with guarded():
        yield


@pytest.fixture(scope="module")
def reg_data(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("reg")
    spec = small_task(tmp, "regression")
    data, *_ = prepared_data(spec, make_cfg(tmp))
    return data


@pytest.fixture(scope="module")
def cls_data(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("cls")
    spec = small_task(tmp, "binary_classification")
    data, *_ = prepared_data(spec, make_cfg(tmp))
    return data


def _cand(task: str = "regression") -> Any:
    return demo_candidates(task, 42, 1)[0]


def _train(
    tmp_path: Path,
    coord: Any,
    data: Any,
    *,
    epochs: int = 3,
    resume: bool = False,
    hooks: TrainerHooks | None = None,
    budget: Any = None,
    fingerprint: str = "fp-1",
    **kw: Any,
) -> Any:
    run_dir = tmp_path / "runs" / RUN_ID
    return train_candidate(
        _cand(data.task_type),
        data,
        budget or tracker(epochs=epochs),
        coord,
        run_dir,
        device="cpu",
        resume=resume,
        run_id=RUN_ID,
        fingerprint=fingerprint,
        max_epochs=epochs,
        patience=epochs,
        seed=42,
        hooks=hooks,
        event_log=EventLog(run_dir / "events.jsonl"),
        epoch_metrics_path=run_dir / "epoch_metrics.jsonl",
        artifact_root=run_dir,
        **kw,
    )


def test_batch_layout_normalizes_last_incomplete_group() -> None:
    sizes, groups = _batch_layout(170, 64, 2)
    assert sizes == [64, 64, 42] and groups == [128, 42]
    sizes, groups = _batch_layout(128, 64, 4)
    assert sizes == [64, 64] and groups == [128]
    assert _batch_layout(5, 2, 1) == ([2, 2, 1], [2, 2, 1])


def test_train_completes_with_finite_metrics(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=3)
        assert res.status == "COMPLETED" and res.epochs_completed == 3 and res.attempts == 1
        assert res.best_epoch is not None and 0 <= res.best_epoch <= 2
        assert is_finite_metrics(
            {k: v for k, v in res.validation_metrics.items() if k in ("mae", "rmse", "r2", "p95_abs_error")}
        )
        assert (
            res.validation_metrics["unit"] == "N"
            and res.device == "cpu"
            and res.amp is False
            and res.seed == 42
        )
        run_dir = tmp_path / "runs" / RUN_ID
        ck = run_dir / "checkpoints" / res.trial_id
        assert (ck / LATEST_NAME).is_file() and (ck / BEST_NAME).is_file() and (ck / "meta.json").is_file()
        rows = read_jsonl(run_dir / "epoch_metrics.jsonl")
        assert [r["epoch"] for r in rows] == [0, 1, 2] and all(r["microbatch"] == 64 for r in rows)
        trial = coord.get_trial(res.trial_id)
        assert trial.status is TrialStatus.COMPLETED and trial.metrics["mae"] == res.validation_metrics["mae"]
        assert coord.is_trial_reusable(trial, run_dir)
        events = [e["event"] for e in read_jsonl(run_dir / "events.jsonl")]
        assert events.count("epoch_completed") == 3 and "trial_completed" in events
        # 완료 trial 은 다시 학습하지 않는다
        with pytest.raises(AgentError) as ei:
            _train(tmp_path, coord, reg_data, epochs=3, resume=True)
        assert ei.value.code == "E_STATE_TRANSITION"
    finally:
        db.close()


def test_classification_trains_and_picks_threshold(tmp_path: Path, cls_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, cls_data, epochs=2)
        assert res.status == "COMPLETED"
        m = res.validation_metrics
        assert res.threshold is not None and 0.0 <= res.threshold <= 1.0 and m["threshold"] == res.threshold
        assert is_finite_metrics(
            {k: m[k] for k in ("average_precision", "roc_auc", "f1", "precision", "recall")}
        )
        assert set(m["confusion_matrix"]) == {"tn", "fp", "fn", "tp"}
    finally:
        db.close()


class OomHooks(TrainerHooks):
    def __init__(self, fail_attempts: set[int]) -> None:
        self.fail_attempts = fail_attempts
        self.seen: list[tuple[int, int, int]] = []

    def on_batch(self, ctx: HookContext, epoch: int, batch_idx: int) -> None:
        self.seen.append((ctx.attempt_no, epoch, batch_idx))
        if ctx.attempt_no in self.fail_attempts and epoch == 0 and batch_idx == 0:
            raise SimulatedOOM()


def test_oom_injection_creates_new_attempts_with_recorded_changes(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        hooks = OomHooks({1, 2})
        res = _train(tmp_path, coord, reg_data, epochs=2, hooks=hooks)
        assert res.status == "COMPLETED" and res.attempts == 3
        assert [c["microbatch"] for c in res.attempt_changes] == [
            {"from": 64, "to": 32},
            {"from": 32, "to": 16},
        ]
        assert [c["accumulation"] for c in res.attempt_changes] == [
            {"from": 1, "to": 2},
            {"from": 2, "to": 4},
        ]
        assert all(c["oom_simulated"] is True for c in res.attempt_changes)
        assert res.microbatch == 16 and res.accumulation == 4
        attempts = coord.list_attempts(res.trial_id)
        assert [a.status for a in attempts] == [
            AttemptStatus.FAILED,
            AttemptStatus.FAILED,
            AttemptStatus.COMPLETED,
        ]
        assert attempts[0].reason == "oom_simulated" and attempts[1].changes["microbatch"] == {
            "from": 64,
            "to": 32,
        }
        assert [d["kind"] for d in res.diagnostics] == ["oom", "oom"]
        rows = read_jsonl(tmp_path / "runs" / RUN_ID / "epoch_metrics.jsonl")
        assert [r["epoch"] for r in rows] == [0, 1] and all(r["accumulation"] == 4 for r in rows)
    finally:
        db.close()


def test_oom_beyond_two_retries_fails_trial(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=2, hooks=OomHooks({1, 2, 3}))
        assert res.status == "FAILED" and res.attempts == 3 and "OOM" in res.reason
        assert coord.get_trial(res.trial_id).status is TrialStatus.FAILED
        assert all(a.status is AttemptStatus.FAILED for a in coord.list_attempts(res.trial_id))
    finally:
        db.close()


class NanHooks(TrainerHooks):
    def __init__(self, attempts: set[int]) -> None:
        self.attempts = attempts

    def inject_non_finite(self, ctx: HookContext, epoch: int, batch_idx: int) -> bool:
        return ctx.attempt_no in self.attempts and epoch == 0 and batch_idx == 1


def test_nan_injection_halves_lr_once(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=2, hooks=NanHooks({1}))
        lr0 = _cand().learning_rate
        assert res.status == "COMPLETED" and res.attempts == 2
        assert (
            res.attempt_changes[0]["learning_rate"] == {"from": lr0, "to": lr0 / 2}
            and res.learning_rate == lr0 / 2
        )
        assert res.diagnostics[0]["kind"] == "non_finite" and res.diagnostics[0]["where"] == "loss"
        assert res.diagnostics[0]["epoch"] == 0 and res.diagnostics[0]["batch"] == 1
        attempts = coord.list_attempts(res.trial_id)
        assert (
            attempts[0].reason == "non_finite_loss_or_grad" and attempts[1].changes["reason"] == "non_finite"
        )
        rows = read_jsonl(tmp_path / "runs" / RUN_ID / "epoch_metrics.jsonl")
        assert [r["epoch"] for r in rows] == [0, 1]
        res2 = _train(
            tmp_path,
            coord,
            reg_data,
            epochs=2,
            hooks=NanHooks({1, 2}),
            fingerprint="fp-2",
            trial_id="run-t-nan2",
        )
        assert res2.status == "FAILED" and res2.attempts == 2 and "NaN" in res2.reason
    finally:
        db.close()


class PauseHooks(TrainerHooks):
    def __init__(self, at_epoch: int, action: str = "pause") -> None:
        self.at_epoch = at_epoch
        self.action = action

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:
        if epoch == self.at_epoch:
            if self.action == "pause":
                ctx.coordinator.request_pause(ctx.run_id)
            elif self.action == "cancel":
                ctx.coordinator.request_cancel(ctx.run_id)
            elif self.action == "crash":
                raise SimulatedCrash()


def test_pause_at_epoch_boundary_then_resume_continues(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=5, hooks=PauseHooks(at_epoch=1))
        assert res.status == "PAUSED" and res.epochs_completed == 2
        trial = coord.get_trial(res.trial_id)
        assert trial.status is TrialStatus.INTERRUPTED and trial.attempts == 1
        assert coord.list_attempts(res.trial_id)[0].status is AttemptStatus.ABORTED
        coord.clear_pause_request(RUN_ID)
        res2 = _train(tmp_path, coord, reg_data, epochs=5, resume=True)
        assert (
            res2.status == "COMPLETED"
            and res2.epochs_completed == 5
            and res2.resumed_from_epoch == 2
            and res2.attempts == 2
        )
        rows = read_jsonl(tmp_path / "runs" / RUN_ID / "epoch_metrics.jsonl")
        assert [r["epoch"] for r in rows] == [0, 1, 2, 3, 4]
        assert [r["attempt_no"] for r in rows] == [1, 1, 2, 2, 2]
        events = read_jsonl(tmp_path / "runs" / RUN_ID / "events.jsonl")
        resumed = [e for e in events if e["event"] == "resumed_from_checkpoint"]
        assert resumed and resumed[0]["next_epoch"] == 2 and resumed[0]["checkpoint"] == LATEST_NAME
    finally:
        db.close()


def test_forced_kill_after_checkpoint_then_resume(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        with pytest.raises(SimulatedCrash):
            _train(tmp_path, coord, reg_data, epochs=4, hooks=PauseHooks(at_epoch=1, action="crash"))
        trial_id = f"{RUN_ID}-{_cand().candidate_id}"
        trial = coord.get_trial(trial_id)
        assert trial.status is TrialStatus.RUNNING  # 정리되지 않은 채 남는다 (kill 시뮬레이션)
        assert coord.list_attempts(trial_id)[0].status is AttemptStatus.RUNNING
        res = _train(tmp_path, coord, reg_data, epochs=4, resume=True)
        assert (
            res.status == "COMPLETED"
            and res.resumed_from_epoch == 2
            and res.epochs_completed == 4
            and res.attempts == 2
        )
        attempts = coord.list_attempts(trial_id)
        assert (
            attempts[0].status is AttemptStatus.ABORTED and attempts[0].reason == "superseded by new attempt"
        )
        rows = read_jsonl(tmp_path / "runs" / RUN_ID / "epoch_metrics.jsonl")
        assert [r["epoch"] for r in rows] == [0, 1, 2, 3]
    finally:
        db.close()


def test_corrupt_latest_falls_back_to_best_and_both_corrupt_fails(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=6, hooks=PauseHooks(at_epoch=2))
        assert res.status == "PAUSED" and res.epochs_completed == 3
        coord.clear_pause_request(RUN_ID)
        ck = tmp_path / "runs" / RUN_ID / "checkpoints" / res.trial_id
        best_next = json.loads((ck / "best.json").read_text(encoding="utf-8"))["next_epoch"]
        latest = ck / LATEST_NAME
        latest.write_bytes(latest.read_bytes()[:100])
        res2 = _train(tmp_path, coord, reg_data, epochs=6, resume=True)
        assert (
            res2.status == "COMPLETED" and res2.resumed_from_epoch == best_next and res2.epochs_completed == 6
        )
        events = [e["event"] for e in read_jsonl(tmp_path / "runs" / RUN_ID / "events.jsonl")]
        assert "checkpoint_corrupt" in events
        # 둘 다 손상: 새 trial 로 시험 (완료 trial 은 재학습 금지이므로)
        res3 = _train(
            tmp_path,
            coord,
            reg_data,
            epochs=4,
            hooks=PauseHooks(at_epoch=1),
            fingerprint="fp-3",
            trial_id="run-t-corrupt",
        )
        assert res3.status == "PAUSED"
        coord.clear_pause_request(RUN_ID)
        ck3 = tmp_path / "runs" / RUN_ID / "checkpoints" / "run-t-corrupt"
        for name in (LATEST_NAME, BEST_NAME):
            (ck3 / name).write_bytes(b"broken")
        with pytest.raises(AgentError) as ei:
            _train(
                tmp_path, coord, reg_data, epochs=4, resume=True, fingerprint="fp-3", trial_id="run-t-corrupt"
            )
        assert ei.value.code == "E_CHECKPOINT_CORRUPT"
    finally:
        db.close()


class ClockHooks(TrainerHooks):
    def __init__(self, clock: FakeClock, advance: float) -> None:
        self.clock = clock
        self.advance = advance

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:
        self.clock.advance(self.advance)


def test_budget_exceeded_stops_safely_and_can_resume(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        clock = FakeClock()
        b = tracker(epochs=4, wall=100, clock=clock)
        res = _train(tmp_path, coord, reg_data, epochs=4, hooks=ClockHooks(clock, 150.0), budget=b)
        assert res.status == "BUDGET_EXCEEDED" and res.epochs_completed == 1
        assert coord.get_trial(res.trial_id).status is TrialStatus.INTERRUPTED
        assert (tmp_path / "runs" / RUN_ID / "checkpoints" / res.trial_id / LATEST_NAME).is_file()
        res2 = _train(tmp_path, coord, reg_data, epochs=4, resume=True)
        assert res2.status == "COMPLETED" and res2.resumed_from_epoch == 1 and res2.epochs_completed == 4
        # can_start_new_work: 다음 epoch 예상 시간이 남은 예산을 넘으면 시작하지 않는다
        clock2 = FakeClock()
        b2 = tracker(epochs=4, wall=100, clock=clock2)
        res3 = _train(
            tmp_path,
            coord,
            reg_data,
            epochs=4,
            hooks=ClockHooks(clock2, 60.0),
            budget=b2,
            fingerprint="fp-b2",
            trial_id="run-t-b2",
        )
        assert res3.status == "BUDGET_EXCEEDED" and 1 <= res3.epochs_completed <= 2
    finally:
        db.close()


def test_cancel_at_epoch_boundary(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=4, hooks=PauseHooks(at_epoch=0, action="cancel"))
        assert res.status == "CANCELLED" and res.epochs_completed == 1
        assert coord.get_trial(res.trial_id).status is TrialStatus.CANCELLED
    finally:
        db.close()


def test_resume_with_changed_fingerprint_rejected(tmp_path: Path, reg_data: Any) -> None:
    db, coord = running_coordinator(tmp_path)
    try:
        res = _train(tmp_path, coord, reg_data, epochs=3, hooks=PauseHooks(at_epoch=0))
        assert res.status == "PAUSED"
        coord.clear_pause_request(RUN_ID)
        with pytest.raises(AgentError) as ei:
            _train(tmp_path, coord, reg_data, epochs=3, resume=True, fingerprint="fp-changed")
        assert ei.value.code == "E_FINGERPRINT_CHANGED"
        # checkpoint 만 다른 hash 를 가진 경우 (trial 은 같은데 파일이 바뀐 상황)
        with pytest.raises(AgentError) as ei2:
            _train(
                tmp_path,
                coord,
                reg_data,
                epochs=3,
                resume=True,
                fingerprint="fp-1",
                config_hash="other-config",
            )
        assert ei2.value.code == "E_FINGERPRINT_CHANGED"
    finally:
        db.close()
