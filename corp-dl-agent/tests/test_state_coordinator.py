"""Coordinator 시험 (BUILD_SPEC [16] C 복구): lease 중복/만료/heartbeat, pause/resume/cancel, trial skip, 자신의 worker 만 정리."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from corp_dl_agent.common import sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.state.budget import BudgetTracker, ResourceBudget
from corp_dl_agent.state.coordinator import Coordinator, compute_fingerprint, make_worker_id
from corp_dl_agent.state.db import StateDB
from corp_dl_agent.state.machine import AttemptStatus, RunStatus, TrialStatus

WORKER_A = "aaaaaaaa-100-0a0a0a0a"
WORKER_B = "bbbbbbbb-200-0b0b0b0b"


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[StateDB]:
    d = StateDB(tmp_path / "상태" / "agent_state.sqlite")
    yield d
    d.close()


@pytest.fixture
def coord(db: StateDB, clock: FakeClock) -> Coordinator:
    return Coordinator(db, WORKER_A, clock=clock)


@pytest.fixture
def other(db: StateDB, clock: FakeClock) -> Coordinator:
    return Coordinator(db, WORKER_B, clock=clock)


def _running(c: Coordinator, run_id: str = "run-1") -> None:
    c.create_run(run_id, "fp-task", "cfg-hash", "regression")
    c.set_status(run_id, RunStatus.VALIDATING)
    c.set_status(run_id, RunStatus.PLANNED)
    c.set_status(run_id, RunStatus.RUNNING)


# ---------------------------------------------------------------- runs / status
def test_create_get_and_duplicate_run(coord: Coordinator) -> None:
    rec = coord.create_run(
        "run-1", "fp", "cfg", "regression", metadata={"synthetic": True, "purpose": "demo"}
    )
    assert rec.status is RunStatus.CREATED and rec.kind == "regression"
    assert rec.metadata == {"synthetic": True, "purpose": "demo"}
    assert coord.get_run("run-1").task_fingerprint == "fp"
    with pytest.raises(AgentError) as ei:
        coord.create_run("run-1", "fp", "cfg", "regression")
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        coord.get_run("missing")
    assert ei2.value.code == "E_INPUT_INVALID"
    assert [r.run_id for r in coord.list_active_runs()] == ["run-1"]
    assert [e["event"] for e in coord.list_events("run-1")] == ["run_created"]


def test_status_transition_violation_leaves_state_untouched(coord: Coordinator) -> None:
    coord.create_run("run-1", "fp", "cfg", "regression")
    with pytest.raises(AgentError) as ei:
        coord.set_status("run-1", RunStatus.RUNNING)
    assert ei.value.code == "E_STATE_TRANSITION"
    assert coord.get_run("run-1").status is RunStatus.CREATED
    assert [e["event"] for e in coord.list_events("run-1")] == ["run_created"]  # 실패한 전이는 기록되지 않음
    with pytest.raises(AgentError):
        coord.set_status("run-1", "NOT_A_STATUS")
    coord.set_status("run-1", "VALIDATING", reason="검증 시작")
    run = coord.get_run("run-1")
    assert run.status is RunStatus.VALIDATING and run.reason == "검증 시작"
    assert coord.list_events("run-1")[-1]["payload"] == {
        "from": "CREATED",
        "to": "VALIDATING",
        "reason": "검증 시작",
    }


def test_list_active_runs_excludes_terminal(coord: Coordinator) -> None:
    _running(coord, "a")
    coord.create_run("b", "fp", "cfg", "classification")
    coord.set_status("b", RunStatus.FAILED, reason="x")
    coord.create_run("c", "fp", "cfg", "classification")
    coord.set_status("c", RunStatus.CANCELLED)
    assert [r.run_id for r in coord.list_active_runs()] == ["a"]
    assert len(coord.list_runs()) == 3


# ---------------------------------------------------------------- leases
def test_lease_duplicate_acquire_rejected(coord: Coordinator, other: Coordinator) -> None:
    _running(coord)
    lease = coord.acquire_lease("run-1", ttl_seconds=30)
    assert lease.worker_id == WORKER_A and lease.expires_at == 1030.0
    assert coord.get_run("run-1").worker_id == WORKER_A
    with pytest.raises(AgentError) as ei:
        other.acquire_lease("run-1", ttl_seconds=30)
    assert ei.value.code == "E_LEASE_HELD"
    assert ei.value.details["holder"] == WORKER_A
    assert ei.value.details["expires_in_seconds"] == 30.0
    held = coord.get_lease("run-1")
    assert held is not None and held.token == lease.token  # 거부된 시도는 lease 를 바꾸지 않는다


def test_expired_lease_is_taken_over_after_forced_kill(
    coord: Coordinator, other: Coordinator, clock: FakeClock
) -> None:
    _running(coord)
    old = coord.acquire_lease("run-1", ttl_seconds=10)
    # worker A 강제 종료 시뮬레이션: release/heartbeat 없이 시간이 지난다
    clock.advance(9)
    with pytest.raises(AgentError):
        other.acquire_lease("run-1", ttl_seconds=10)  # 아직 유효
    clock.advance(2)
    new = other.acquire_lease("run-1", ttl_seconds=10)
    assert new.worker_id == WORKER_B and new.token != old.token
    assert coord.get_run("run-1").worker_id == WORKER_B
    events = coord.list_events("run-1")
    takeover = [e for e in events if e["event"] == "lease_takeover"]
    assert takeover and takeover[-1]["payload"]["from_worker"] == WORKER_A
    # 되살아난 옛 worker 는 heartbeat/검증에 실패해야 한다
    with pytest.raises(AgentError) as ei:
        coord.heartbeat(old)
    assert ei.value.code == "E_LEASE_HELD"
    with pytest.raises(AgentError):
        coord.assert_lease_valid(old)
    assert coord.release(old) is False  # 남의 lease 는 지우지 못한다
    assert coord.get_lease("run-1") is not None
    other.assert_lease_valid(new)


def test_heartbeat_extends_lease(coord: Coordinator, clock: FakeClock) -> None:
    _running(coord)
    lease = coord.acquire_lease("run-1", ttl_seconds=10)
    clock.advance(5)
    renewed = coord.heartbeat(lease)
    assert renewed.expires_at == 1015.0 and renewed.heartbeat_at == 1005.0
    stored = coord.get_lease("run-1")
    assert stored is not None and stored.expires_at == 1015.0 and stored.token == lease.token
    assert stored.is_valid(clock()) and not stored.is_valid(1015.0)
    # 늦은 heartbeat (만료 후, 아직 아무도 인수하지 않음) 는 갱신되며 이벤트를 남긴다
    clock.advance(20)
    late = coord.heartbeat(renewed)
    assert late.expires_at == 1035.0
    assert coord.list_events("run-1")[-1]["event"] == "lease_late_heartbeat"


def test_release_and_reacquire_by_other_worker(coord: Coordinator, other: Coordinator) -> None:
    _running(coord)
    lease = coord.acquire_lease("run-1", ttl_seconds=10)
    assert other.release(lease) is False
    assert coord.release(lease) is True
    assert coord.get_lease("run-1") is None
    assert coord.get_run("run-1").worker_id is None
    assert coord.release(lease) is False  # 이미 해제됨
    assert other.acquire_lease("run-1", ttl_seconds=10).worker_id == WORKER_B


def test_same_worker_reacquire_renews(coord: Coordinator) -> None:
    _running(coord)
    first = coord.acquire_lease("run-1", ttl_seconds=10)
    second = coord.acquire_lease("run-1", ttl_seconds=20)
    assert second.token != first.token and second.expires_at == 1020.0
    assert coord.list_events("run-1")[-1]["event"] == "lease_renewed"
    with pytest.raises(AgentError):
        coord.heartbeat(first)  # 옛 token 은 무효


def test_lease_rejected_on_terminal_run_or_bad_ttl(coord: Coordinator) -> None:
    coord.create_run("done", "fp", "cfg", "regression")
    coord.set_status("done", RunStatus.CANCELLED)
    with pytest.raises(AgentError) as ei:
        coord.acquire_lease("done", 10)
    assert ei.value.code == "E_STATE_TRANSITION"
    _running(coord)
    with pytest.raises(AgentError) as ei2:
        coord.acquire_lease("run-1", 0)
    assert ei2.value.code == "E_INPUT_INVALID"


def test_expire_stale_leases_only_removes_expired(
    coord: Coordinator, other: Coordinator, clock: FakeClock
) -> None:
    _running(coord, "run-1")
    _running(other, "run-2")
    coord.acquire_lease("run-1", ttl_seconds=10)
    other.acquire_lease("run-2", ttl_seconds=100)
    assert coord.expire_stale_leases() == []
    clock.advance(50)
    removed = coord.expire_stale_leases()
    assert [r["run_id"] for r in removed] == ["run-1"]
    assert removed[0]["worker_id"] == WORKER_A and removed[0]["expired_by_seconds"] == 40.0
    assert coord.get_lease("run-1") is None
    assert coord.get_lease("run-2") is not None
    assert coord.list_events("run-1")[-1]["event"] == "lease_expired"
    assert other.expire_stale_leases(now=2000.0)[0]["run_id"] == "run-2"


# ---------------------------------------------------------------- pause / resume / cancel
def test_pause_request_then_paused_then_resume(coord: Coordinator) -> None:
    _running(coord)
    assert coord.is_pause_requested("run-1") is False
    coord.request_pause("run-1")
    assert coord.is_pause_requested("run-1") is True
    # worker 가 epoch 경계에서 플래그를 보고 PAUSED 로 전이한다
    paused = coord.set_status("run-1", RunStatus.PAUSED, reason="epoch 3 경계")
    assert paused.status is RunStatus.PAUSED
    assert paused.previous_status is RunStatus.RUNNING
    assert paused.pause_requested is False  # 처리된 요청은 해제
    assert coord.request_pause("run-1").status is RunStatus.PAUSED  # 이미 PAUSED 면 무해
    resumed = coord.resume("run-1")
    assert resumed.status is RunStatus.RUNNING and resumed.previous_status is None
    events = [e["event"] for e in coord.list_events("run-1")]
    assert events[-3:] == ["pause_requested", "status_changed", "status_changed"]
    assert coord.list_events("run-1")[-1]["payload"]["reason"] == "resume"


def test_pause_from_planned_resumes_to_planned(coord: Coordinator) -> None:
    coord.create_run("run-1", "fp", "cfg", "regression")
    coord.set_status("run-1", RunStatus.VALIDATING)
    coord.set_status("run-1", RunStatus.PLANNED)
    coord.set_status("run-1", RunStatus.PAUSED)
    assert coord.resume("run-1").status is RunStatus.PLANNED


def test_cancel_request_then_cancelled(coord: Coordinator) -> None:
    _running(coord)
    coord.request_cancel("run-1")
    assert coord.is_cancel_requested("run-1") is True
    coord.set_status("run-1", RunStatus.CANCELLED, reason="사용자 취소")
    run = coord.get_run("run-1")
    assert run.status is RunStatus.CANCELLED and run.cancel_requested is False
    assert coord.list_active_runs() == []
    with pytest.raises(AgentError) as ei:
        coord.request_cancel("run-1")
    assert ei.value.code == "E_STATE_TRANSITION"
    with pytest.raises(AgentError):
        coord.request_pause("run-1")


def test_resume_refused_when_cancel_requested_or_not_resumable(coord: Coordinator) -> None:
    _running(coord)
    coord.set_status("run-1", RunStatus.PAUSED)
    coord.request_cancel("run-1")
    with pytest.raises(AgentError) as ei:
        coord.resume("run-1")
    assert ei.value.code == "E_STATE_TRANSITION"
    assert coord.get_run("run-1").status is RunStatus.PAUSED
    coord.set_status("run-1", RunStatus.CANCELLED)
    _running(coord, "run-2")
    with pytest.raises(AgentError) as ei2:
        coord.resume("run-2")  # RUNNING 은 재개 대상이 아니다
    assert ei2.value.code == "E_STATE_TRANSITION"


def test_pause_request_survives_running_transitions_until_paused(coord: Coordinator) -> None:
    _running(coord)
    coord.request_pause("run-1")
    coord.set_status("run-1", RunStatus.EVALUATING)
    assert coord.is_pause_requested("run-1") is True  # 아직 처리 안 됨
    coord.set_status("run-1", RunStatus.PAUSED)
    assert coord.is_pause_requested("run-1") is False
    assert coord.resume("run-1").status is RunStatus.EVALUATING


# ---------------------------------------------------------------- budget
def test_budget_exceeded_sets_status_and_can_resume(coord: Coordinator, clock: FakeClock) -> None:
    _running(coord)
    tracker = BudgetTracker(ResourceBudget.demo(wall_time_seconds=100), clock=clock, label="run-1")
    tracker.start()
    clock.advance(101)
    assert tracker.can_start_new_work(1) is False
    with pytest.raises(AgentError) as ei:
        tracker.check()
    assert ei.value.code == "E_BUDGET_EXCEEDED"
    run = coord.set_status("run-1", RunStatus.BUDGET_EXCEEDED, reason=ei.value.message)
    assert run.status is RunStatus.BUDGET_EXCEEDED and run.previous_status is RunStatus.RUNNING
    assert "wall_time" in run.reason
    assert coord.resume("run-1", reason="예산 증액 후 재개").status is RunStatus.RUNNING


# ---------------------------------------------------------------- trials / attempts
def test_trial_and_attempt_lifecycle(coord: Coordinator) -> None:
    _running(coord)
    trial = coord.create_trial("run-1", "t1", {"hidden_dims": [64, 32], "lr": 1e-3}, "fp-t1")
    assert trial.status is TrialStatus.PENDING and trial.attempts == 0
    assert trial.candidate_config == {"hidden_dims": [64, 32], "lr": 1e-3}
    with pytest.raises(AgentError) as dup:
        coord.create_trial("run-1", "t1", {}, "fp-t1")
    assert dup.value.code == "E_INPUT_INVALID"

    a1 = coord.create_attempt("t1")
    assert a1 == "t1-a01"
    t = coord.get_trial("t1")
    assert t.status is TrialStatus.RUNNING and t.worker_id == WORKER_A and t.attempts == 1

    # OOM 정책 모의: 실패 attempt 를 닫고 microbatch 를 바꾼 새 attempt (변경 기록)
    coord.finish_attempt(a1, AttemptStatus.FAILED, reason="oom (모의 주입)")
    a2 = coord.create_attempt("t1", changes={"microbatch": 16, "accumulation": 2})
    assert a2 == "t1-a02"
    assert coord.get_attempt(a2).changes == {"microbatch": 16, "accumulation": 2}
    a3 = coord.create_attempt("t1")  # 열린 a2 는 superseded → ABORTED
    assert coord.get_attempt(a2).status is AttemptStatus.ABORTED
    assert "superseded" in coord.get_attempt(a2).reason

    done = coord.finish_trial("t1", TrialStatus.COMPLETED, {"mae": 0.5, "epochs": 7}, {"model.json": "abc"})
    assert done.status is TrialStatus.COMPLETED and done.metrics == {"mae": 0.5, "epochs": 7}
    assert done.artifact_hashes == {"model.json": "abc"} and done.finished_at is not None
    assert coord.get_attempt(a3).status is AttemptStatus.COMPLETED
    assert [a.attempt_no for a in coord.list_attempts("t1")] == [1, 2, 3]
    assert [x.trial_id for x in coord.list_trials("run-1")] == ["t1"]

    with pytest.raises(AgentError) as ei:
        coord.finish_trial("t1", TrialStatus.FAILED)
    assert ei.value.code == "E_STATE_TRANSITION"
    with pytest.raises(AgentError):
        coord.create_attempt("t1")
    with pytest.raises(AgentError):
        coord.finish_trial("t1", TrialStatus.RUNNING)  # 종료 상태만 허용
    with pytest.raises(AgentError):
        coord.finish_attempt(a3, AttemptStatus.FAILED)  # 이미 닫힘


def test_completed_trial_rejects_non_finite_metrics(coord: Coordinator) -> None:
    _running(coord)
    coord.create_trial("run-1", "t1", {}, "fp")
    coord.create_attempt("t1")
    with pytest.raises(AgentError) as ei:
        coord.finish_trial("t1", TrialStatus.COMPLETED, {"mae": float("nan")})
    assert ei.value.code == "E_INPUT_INVALID"
    assert coord.get_trial("t1").status is TrialStatus.RUNNING
    failed = coord.finish_trial("t1", TrialStatus.FAILED, {"loss": float("inf")}, reason="발산")
    assert failed.metrics == {"loss": "Infinity"}


def test_completed_trial_with_same_fingerprint_is_skipped_after_artifact_hash_check(
    coord: Coordinator, tmp_path: Path
) -> None:
    art = tmp_path / "산출물" / "t1"
    art.mkdir(parents=True)
    (art / "model.json").write_text('{"w": [1, 2]}', encoding="utf-8")
    (art / "export" / "manifest.json").parent.mkdir()
    (art / "export" / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
    hashes = {
        "model.json": sha256_file(art / "model.json"),
        "export/manifest.json": sha256_file(art / "export" / "manifest.json"),
    }
    fp = compute_fingerprint(
        task="clip-regression",
        data_hash="d",
        split_hash="s",
        code_hash="c",
        lock_hash="l",
        model_config={"h": [64]},
        seed=42,
    )
    _running(coord, "run-1")
    coord.create_trial("run-1", "t1", {"h": [64]}, fp)
    coord.create_attempt("t1")
    coord.finish_trial("t1", TrialStatus.COMPLETED, {"mae": 0.25}, hashes)

    # 새 run 에서 같은 fingerprint 를 만나면 완료 trial 을 찾고 산출물 hash 까지 검증한 뒤 건너뛴다
    _running(coord, "run-2")
    found = coord.find_completed_trial(fp)
    assert found is not None and found.trial_id == "t1"
    assert coord.verify_trial_artifacts(found, art) == {"model.json": "ok", "export/manifest.json": "ok"}
    assert coord.is_trial_reusable(found, art) is True
    coord.create_trial("run-2", "t2", {"h": [64]}, fp)
    skipped = coord.mark_trial_skipped("t2", found)
    assert skipped.status is TrialStatus.SKIPPED and skipped.reason == "reused:t1"
    assert skipped.metrics == {"mae": 0.25} and skipped.artifact_hashes == hashes
    assert coord.find_completed_trial(fp, run_id="run-2") is None  # SKIPPED 는 재사용 원본이 아니다


def test_artifact_hash_mismatch_or_missing_forces_rerun(coord: Coordinator, tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    (art / "model.json").write_text("v1", encoding="utf-8")
    _running(coord)
    coord.create_trial("run-1", "t1", {}, "fp")
    coord.create_attempt("t1")
    coord.finish_trial(
        "t1", TrialStatus.COMPLETED, {"mae": 1.0}, {"model.json": sha256_file(art / "model.json")}
    )
    found = coord.find_completed_trial("fp")
    assert found is not None and coord.is_trial_reusable(found, art) is True
    (art / "model.json").write_text("v2-tampered", encoding="utf-8")  # 산출물 변조
    assert coord.verify_trial_artifacts(found, art) == {"model.json": "mismatch"}
    assert coord.is_trial_reusable(found, art) is False  # → 재실행
    (art / "model.json").unlink()
    assert coord.verify_trial_artifacts(found, art) == {"model.json": "missing"}
    assert coord.is_trial_reusable(found, art) is False
    # 산출물 기록이 없는 완료 trial 은 검증할 수 없으므로 재사용하지 않는다
    coord.create_trial("run-1", "t2", {}, "fp-none")
    coord.create_attempt("t2")
    coord.finish_trial("t2", TrialStatus.COMPLETED, {"mae": 1.0}, {})
    assert coord.is_trial_reusable(coord.find_completed_trial("fp-none"), art) is False
    assert coord.is_trial_reusable(None, art) is False
    # artifact_dir 밖을 가리키는 항목은 검증하지 않는다
    outside = found.model_copy(update={"artifact_hashes": {"../escape.json": "x"}})
    assert coord.verify_trial_artifacts(outside, art) == {"../escape.json": "outside"}


def test_find_completed_trial_ignores_failed_and_other_fingerprints(coord: Coordinator) -> None:
    _running(coord)
    coord.create_trial("run-1", "t-fail", {}, "fp-x")
    coord.create_attempt("t-fail")
    coord.finish_trial("t-fail", TrialStatus.FAILED, reason="nan")
    coord.create_trial("run-1", "t-other", {}, "fp-y")
    coord.create_attempt("t-other")
    coord.finish_trial("t-other", TrialStatus.COMPLETED, {"mae": 2.0}, {"m": "h"})
    assert coord.find_completed_trial("fp-x") is None
    hit = coord.find_completed_trial("fp-y")
    assert hit is not None and hit.trial_id == "t-other" and hit.attempts == 1


# ---------------------------------------------------------------- cleanup
def test_cleanup_own_does_not_touch_other_workers_lease(coord: Coordinator, other: Coordinator) -> None:
    _running(coord, "run-1")
    _running(other, "run-2")
    lease_a = coord.acquire_lease("run-1", ttl_seconds=60)
    coord.create_trial("run-1", "t1", {}, "fp")
    a1 = coord.create_attempt("t1")
    other.acquire_lease("run-2", ttl_seconds=60)
    other.create_trial("run-2", "t2", {}, "fp2")
    a2 = other.create_attempt("t2")

    summary = other.cleanup_own()
    assert summary["leases_released"] == ["run-2"]
    assert summary["attempts_aborted"] == [a2] and summary["trials_interrupted"] == ["t2"]
    assert summary["other_worker_leases_untouched"] == 1
    # worker A 의 lease/attempt/trial 은 그대로다
    held = coord.get_lease("run-1")
    assert held is not None and held.worker_id == WORKER_A and held.token == lease_a.token
    coord.heartbeat(lease_a)
    assert coord.get_attempt(a1).status is AttemptStatus.RUNNING
    assert coord.get_trial("t1").status is TrialStatus.RUNNING
    assert coord.get_run("run-1").worker_id == WORKER_A
    assert coord.get_run("run-2").worker_id is None
    assert coord.get_trial("t2").status is TrialStatus.INTERRUPTED
    # 중단된 trial 은 새 attempt 로 재개할 수 있다
    other.acquire_lease("run-2", ttl_seconds=60)
    assert other.create_attempt("t2") == "t2-a02"
    assert coord.get_trial("t2").status is TrialStatus.RUNNING


def test_cleanup_own_scoped_to_run(coord: Coordinator) -> None:
    _running(coord, "run-1")
    _running(coord, "run-2")
    coord.acquire_lease("run-1", ttl_seconds=60)
    coord.acquire_lease("run-2", ttl_seconds=60)
    summary = coord.cleanup_own("run-1")
    assert summary["leases_released"] == ["run-1"]
    assert coord.get_lease("run-1") is None and coord.get_lease("run-2") is not None
    assert coord.list_events("run-1")[-1]["event"] == "worker_cleanup"
    assert coord.cleanup_own("run-1")["leases_released"] == []  # 멱등


# ---------------------------------------------------------------- events / fingerprint / worker id
def test_events_mask_secrets_and_keep_order(coord: Coordinator) -> None:
    _running(coord)
    token = "sk-verysecrettoken123456"
    coord.record_event(
        "run-1", "gateway_call", {"authorization": f"Bearer {token}", "note": f"used {token}", "n": 1}
    )
    ev = coord.list_events("run-1")[-1]
    assert ev["event"] == "gateway_call" and ev["worker_id"] == WORKER_A
    assert token not in str(ev["payload"])
    assert ev["payload"]["n"] == 1
    assert "***" in ev["payload"]["note"]
    ids = [e["event_id"] for e in coord.list_events("run-1")]
    assert ids == sorted(ids)
    assert len(coord.list_events("run-1", limit=2)) == 2


def test_fingerprint_is_deterministic_and_resume_guard(coord: Coordinator) -> None:
    def fp(*, seed: int = 42, lock_hash: str = "l1") -> str:
        return compute_fingerprint(
            task={"id": "clip"},
            data_hash="d1",
            split_hash="s1",
            code_hash="c1",
            lock_hash=lock_hash,
            model_config={"h": [64]},
            seed=seed,
        )

    fp1 = fp()
    fp2 = fp()
    fp_seed = fp(seed=43)
    fp_lock = fp(lock_hash="l2")
    assert fp1 == fp2 and len(fp1) == 64
    assert fp1 != fp_seed and fp1 != fp_lock
    coord.create_run("run-1", fp1, "cfg-1", "regression")
    assert coord.assert_fingerprint_matches("run-1", fp1, "cfg-1").run_id == "run-1"
    with pytest.raises(AgentError) as ei:
        coord.assert_fingerprint_matches("run-1", fp_lock, "cfg-1")
    assert ei.value.code == "E_FINGERPRINT_CHANGED"
    with pytest.raises(AgentError):
        coord.assert_fingerprint_matches("run-1", fp1, "cfg-2")


def test_worker_id_format(db: StateDB) -> None:
    pattern = re.compile(r"^[0-9a-f]{8}-\d+-[0-9a-f]{8}$")
    w1, w2 = make_worker_id(), make_worker_id()
    assert pattern.match(w1) and pattern.match(w2)
    assert w1 != w2
    assert w1.split("-")[1] == w2.split("-")[1]  # 같은 프로세스 → 같은 pid
    assert pattern.match(Coordinator(db).worker_id)  # 기본값도 같은 형식
    with pytest.raises(AgentError) as ei:
        Coordinator(db, "   ")
    assert ei.value.code == "E_INPUT_INVALID"


# ---------------------------------------------------------------- multi-connection (프로세스 분리 모사)
def test_lease_dedup_across_two_connections(tmp_path: Path) -> None:
    p = tmp_path / "공유 상태" / "state.sqlite"
    clock = FakeClock()
    db_a = StateDB(p)
    db_b = StateDB(p)
    try:
        a = Coordinator(db_a, WORKER_A, clock=clock)
        b = Coordinator(db_b, WORKER_B, clock=clock)
        _running(a)
        a.acquire_lease("run-1", ttl_seconds=10)
        with pytest.raises(AgentError) as ei:
            b.acquire_lease("run-1", ttl_seconds=10)
        assert ei.value.code == "E_LEASE_HELD"
        assert b.get_run("run-1").worker_id == WORKER_A  # 다른 connection 에서도 같은 상태를 본다
        clock.advance(11)
        assert b.acquire_lease("run-1", ttl_seconds=10).worker_id == WORKER_B
        assert a.get_run("run-1").worker_id == WORKER_B
        info = db_a.backup_to(tmp_path / "백업" / "state.sqlite")
        assert info["integrity"] == "ok" and info["runs"] == 1
    finally:
        db_a.close()
        db_b.close()
