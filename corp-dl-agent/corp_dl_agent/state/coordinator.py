"""run / trial / attempt / lease 조정자.

- 모든 쓰기는 StateDB.transaction() (BEGIN IMMEDIATE) 안에서 수행한다.
- lease: run 당 하나. worker_id/token/expires_at/heartbeat. 만료 전에는 다른 worker 가 획득할 수 없다(E_LEASE_HELD).
  만료(강제 종료) 후에는 다른 worker 가 인수하며 이전 worker 의 heartbeat 는 실패한다.
- trial(후보 실험) 과 attempt(실행 시도) 를 분리한다. 동일 fingerprint 의 완료 trial 은 산출물 hash 검증 후 재사용한다.
- pause/cancel 은 요청 플래그이며 실제 전이는 worker 가 epoch 경계에서 수행한다.
- 자신의 worker 가 남긴 lease/attempt 만 정리한다 (cleanup_own). 다른 worker 는 만료 전에는 건드리지 않는다.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import socket
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import Field

from corp_dl_agent.common import StrictModel, canonical_json, now_iso, sha256_file, sha256_text
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.secrets import REGISTRY
from corp_dl_agent.state.db import StateDB
from corp_dl_agent.state.machine import (
    RESUMABLE_STATUSES,
    TERMINAL_STATUSES,
    TERMINAL_TRIAL_STATUSES,
    AttemptStatus,
    RunStatus,
    TrialStatus,
    assert_attempt_transition,
    assert_transition,
    assert_trial_transition,
    coerce_attempt_status,
    coerce_run_status,
    coerce_trial_status,
    is_terminal,
)


# ---------------------------------------------------------------------- records
class RunRecord(StrictModel):
    run_id: str
    kind: str
    status: RunStatus
    previous_status: RunStatus | None = None
    task_fingerprint: str
    config_hash: str
    worker_id: str | None = None
    pause_requested: bool = False
    cancel_requested: bool = False
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class Lease(StrictModel):
    run_id: str
    worker_id: str
    token: str
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    ttl_seconds: float

    def is_valid(self, now: float) -> bool:
        return now < self.expires_at


class TrialRecord(StrictModel):
    trial_id: str
    run_id: str
    fingerprint: str
    status: TrialStatus
    candidate_config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    worker_id: str | None = None
    attempts: int = 0
    reason: str = ""
    created_at: str
    updated_at: str
    finished_at: str | None = None


class AttemptRecord(StrictModel):
    attempt_id: str
    trial_id: str
    attempt_no: int
    status: AttemptStatus
    worker_id: str | None = None
    changes: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    started_at: str
    finished_at: str | None = None


# ---------------------------------------------------------------------- helpers
def make_worker_id() -> str:
    """ "<hostname-hash>-<pid>-<random8>". hostname 자체는 기록하지 않는다."""
    host_hash = sha256_text(socket.gethostname())[:8]
    return f"{host_hash}-{os.getpid()}-{secrets.token_hex(4)}"


def compute_fingerprint(
    *,
    task: Any,
    data_hash: str,
    split_hash: str,
    code_hash: str,
    lock_hash: str,
    model_config: Any,
    seed: int,
) -> str:
    """sha256(canonical_json({task, data_hash, split_hash, code_hash, lock_hash, model_config, seed}))."""
    payload = {
        "task": task,
        "data_hash": data_hash,
        "split_hash": split_hash,
        "code_hash": code_hash,
        "lock_hash": lock_hash,
        "model_config": model_config,
        "seed": seed,
    }
    return sha256_text(canonical_json(payload))


def _dumps(obj: Any) -> str:
    return json.dumps(REGISTRY.mask_obj(obj), ensure_ascii=False, sort_keys=True, default=str)


def _loads(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        return {"_unparseable": True}
    return val if isinstance(val, dict) else {"_value": val}


def _sanitize_metrics(metrics: Mapping[str, Any] | None, *, strict_finite: bool) -> dict[str, Any]:
    """JSON 에 저장 가능한 metrics. 완료 trial 은 유한 값만 허용, 그 외는 NaN/Inf 를 문자열 표시로 보존한다."""
    out: dict[str, Any] = {}
    for k, v in (metrics or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            out[str(k)] = v
            continue
        fv = float(v)
        if math.isfinite(fv):
            out[str(k)] = v
        elif strict_finite:
            raise AgentError(
                "E_INPUT_INVALID",
                f"완료 trial 의 metric '{k}' 가 유한하지 않습니다 ({fv!r}). 완료로 기록하지 않습니다.",
                details={"metric": str(k)},
            )
        else:
            out[str(k)] = "NaN" if math.isnan(fv) else ("Infinity" if fv > 0 else "-Infinity")
    return out


def _row_to_run(row: Any) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        status=RunStatus(str(row["status"])),
        previous_status=RunStatus(str(row["previous_status"])) if row["previous_status"] else None,
        task_fingerprint=str(row["task_fingerprint"]),
        config_hash=str(row["config_hash"]),
        worker_id=str(row["worker_id"]) if row["worker_id"] else None,
        pause_requested=bool(row["pause_requested"]),
        cancel_requested=bool(row["cancel_requested"]),
        reason=str(row["reason"] or ""),
        metadata=_loads(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_lease(row: Any) -> Lease:
    return Lease(
        run_id=str(row["run_id"]),
        worker_id=str(row["worker_id"]),
        token=str(row["token"]),
        acquired_at=float(row["acquired_at"]),
        heartbeat_at=float(row["heartbeat_at"]),
        expires_at=float(row["expires_at"]),
        ttl_seconds=float(row["ttl_seconds"]),
    )


def _row_to_trial(row: Any, attempts: int) -> TrialRecord:
    hashes = _loads(row["artifact_hashes_json"])
    return TrialRecord(
        trial_id=str(row["trial_id"]),
        run_id=str(row["run_id"]),
        fingerprint=str(row["fingerprint"]),
        status=TrialStatus(str(row["status"])),
        candidate_config=_loads(row["candidate_json"]),
        metrics=_loads(row["metrics_json"]),
        artifact_hashes={str(k): str(v) for k, v in hashes.items()},
        worker_id=str(row["worker_id"]) if row["worker_id"] else None,
        attempts=int(attempts),
        reason=str(row["reason"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )


def _row_to_attempt(row: Any) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=str(row["attempt_id"]),
        trial_id=str(row["trial_id"]),
        attempt_no=int(row["attempt_no"]),
        status=AttemptStatus(str(row["status"])),
        worker_id=str(row["worker_id"]) if row["worker_id"] else None,
        changes=_loads(row["changes_json"]),
        reason=str(row["reason"] or ""),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )


# ---------------------------------------------------------------------- coordinator
class Coordinator:
    def __init__(
        self,
        db: StateDB,
        worker_id: str | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.worker_id = worker_id or make_worker_id()
        if not self.worker_id.strip():
            raise AgentError("E_INPUT_INVALID", "worker_id 가 비어 있습니다")
        self._clock: Callable[[], float] = clock or time.time

    def now(self) -> float:
        return float(self._clock())

    # ------------------------------------------------------------------ events
    def record_event(self, run_id: str | None, event: str, payload: Mapping[str, Any] | None = None) -> int:
        """이벤트 기록 (payload 는 secret 마스킹 후 JSON). 반환: event_id."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO events(run_id, ts, event, worker_id, payload_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, now_iso(), event, self.worker_id, _dumps(dict(payload or {}))),
            )
            return int(cur.lastrowid or 0)

    def list_events(self, run_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT event_id, run_id, ts, event, worker_id, payload_json FROM events WHERE run_id=? ORDER BY event_id",
            (run_id,),
        )
        out = [
            {
                "event_id": int(r["event_id"]),
                "run_id": r["run_id"],
                "ts": r["ts"],
                "event": r["event"],
                "worker_id": r["worker_id"],
                "payload": _loads(r["payload_json"]),
            }
            for r in rows
        ]
        if limit is not None:
            out = out[-int(limit) :]
        return out

    # ------------------------------------------------------------------ runs
    def create_run(
        self,
        run_id: str,
        task_fingerprint: str,
        config_hash: str,
        kind: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        if not run_id or not run_id.strip():
            raise AgentError("E_INPUT_INVALID", "run_id 가 비어 있습니다")
        ts = now_iso()
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is not None:
                raise AgentError(
                    "E_INPUT_INVALID", f"이미 존재하는 run_id 입니다: {run_id}", details={"run_id": run_id}
                )
            conn.execute(
                "INSERT INTO runs(run_id, kind, status, previous_status, task_fingerprint, config_hash, worker_id,"
                " pause_requested, cancel_requested, reason, metadata_json, created_at, updated_at)"
                " VALUES (?, ?, ?, NULL, ?, ?, NULL, 0, 0, '', ?, ?, ?)",
                (
                    run_id,
                    kind,
                    RunStatus.CREATED.value,
                    task_fingerprint,
                    config_hash,
                    _dumps(dict(metadata or {})),
                    ts,
                    ts,
                ),
            )
            self.record_event(
                run_id,
                "run_created",
                {"kind": kind, "task_fingerprint": task_fingerprint, "config_hash": config_hash},
            )
            return self._get_run_locked(conn, run_id)

    def _get_run_locked(self, conn: Any, run_id: str) -> RunRecord:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise AgentError(
                "E_INPUT_INVALID", f"run 을 찾을 수 없습니다: {run_id}", details={"run_id": run_id}
            )
        return _row_to_run(row)

    def get_run(self, run_id: str) -> RunRecord:
        row = self.db.query_one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if row is None:
            raise AgentError(
                "E_INPUT_INVALID", f"run 을 찾을 수 없습니다: {run_id}", details={"run_id": run_id}
            )
        return _row_to_run(row)

    def list_runs(self) -> list[RunRecord]:
        return [_row_to_run(r) for r in self.db.query("SELECT * FROM runs ORDER BY created_at, run_id")]

    def list_active_runs(self) -> list[RunRecord]:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        rows = self.db.query(
            f"SELECT * FROM runs WHERE status NOT IN ({placeholders}) ORDER BY created_at, run_id",  # noqa: S608 - placeholders only
            tuple(s.value for s in TERMINAL_STATUSES),
        )
        return [_row_to_run(r) for r in rows]

    def set_status(self, run_id: str, new_status: RunStatus | str, reason: str = "") -> RunRecord:
        """상태 전이표 검사 후 갱신. PAUSED/BUDGET_EXCEEDED 로 갈 때 이전 상태를 previous_status 에 보관한다."""
        new = coerce_run_status(new_status)
        ts = now_iso()
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            assert_transition(run.status, new)
            previous = run.status.value if new in RESUMABLE_STATUSES else None
            pause_requested = (
                run.pause_requested and new not in RESUMABLE_STATUSES and new not in TERMINAL_STATUSES
            )
            cancel_requested = (
                run.cancel_requested and new is not RunStatus.CANCELLED and new not in TERMINAL_STATUSES
            )
            conn.execute(
                "UPDATE runs SET status=?, previous_status=?, reason=?, pause_requested=?, cancel_requested=?, updated_at=?"
                " WHERE run_id=?",
                (new.value, previous, reason, int(pause_requested), int(cancel_requested), ts, run_id),
            )
            self.record_event(
                run_id, "status_changed", {"from": run.status.value, "to": new.value, "reason": reason}
            )
            return self._get_run_locked(conn, run_id)

    def resume(self, run_id: str, reason: str = "") -> RunRecord:
        """PAUSED/BUDGET_EXCEEDED 에서 직전 상태로 복귀. cancel 요청이 있으면 재개하지 않는다."""
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            if run.status not in RESUMABLE_STATUSES:
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"재개할 수 없는 상태입니다: {run.status.value}",
                    details={"run_id": run_id, "status": run.status.value},
                )
            if run.cancel_requested:
                raise AgentError(
                    "E_STATE_TRANSITION",
                    "cancel 요청이 있는 run 은 재개하지 않습니다",
                    details={"run_id": run_id},
                )
            target = run.previous_status or RunStatus.RUNNING
            if target not in (RunStatus.PLANNED, RunStatus.RUNNING, RunStatus.EVALUATING):
                target = RunStatus.RUNNING
            return self.set_status(run_id, target, reason or "resume")

    def assert_fingerprint_matches(self, run_id: str, task_fingerprint: str, config_hash: str) -> RunRecord:
        """재개 전 코드/데이터/lock/설정이 같은지 확인. 다르면 E_FINGERPRINT_CHANGED (자동 재개 금지)."""
        run = self.get_run(run_id)
        if run.task_fingerprint != task_fingerprint or run.config_hash != config_hash:
            raise AgentError(
                "E_FINGERPRINT_CHANGED",
                f"run {run_id} 의 fingerprint/config hash 가 현재 환경과 다릅니다",
                details={
                    "run_id": run_id,
                    "stored_fingerprint": run.task_fingerprint[:16],
                    "current_fingerprint": task_fingerprint[:16],
                    "stored_config_hash": run.config_hash[:16],
                    "current_config_hash": config_hash[:16],
                },
            )
        return run

    # ------------------------------------------------------------------ pause / cancel flags
    def request_pause(self, run_id: str) -> RunRecord:
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            if is_terminal(run.status):
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 run 은 pause 할 수 없습니다: {run.status.value}",
                    details={"run_id": run_id},
                )
            if run.status is RunStatus.PAUSED:
                return run
            conn.execute(
                "UPDATE runs SET pause_requested=1, updated_at=? WHERE run_id=?", (now_iso(), run_id)
            )
            self.record_event(run_id, "pause_requested", {})
            return self._get_run_locked(conn, run_id)

    def request_cancel(self, run_id: str) -> RunRecord:
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            if is_terminal(run.status):
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 run 은 cancel 할 수 없습니다: {run.status.value}",
                    details={"run_id": run_id},
                )
            conn.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE run_id=?", (now_iso(), run_id)
            )
            self.record_event(run_id, "cancel_requested", {})
            return self._get_run_locked(conn, run_id)

    def is_pause_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).pause_requested

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).cancel_requested

    def clear_pause_request(self, run_id: str) -> RunRecord:
        with self.db.transaction() as conn:
            self._get_run_locked(conn, run_id)
            conn.execute(
                "UPDATE runs SET pause_requested=0, updated_at=? WHERE run_id=?", (now_iso(), run_id)
            )
            return self._get_run_locked(conn, run_id)

    # ------------------------------------------------------------------ leases
    def get_lease(self, run_id: str) -> Lease | None:
        row = self.db.query_one("SELECT * FROM leases WHERE run_id=?", (run_id,))
        return _row_to_lease(row) if row is not None else None

    def acquire_lease(self, run_id: str, ttl_seconds: float) -> Lease:
        """run 의 lease 획득. 유효한 lease 를 다른 worker 가 갖고 있으면 E_LEASE_HELD. 만료된 lease 는 인수한다."""
        if ttl_seconds <= 0:
            raise AgentError("E_INPUT_INVALID", "lease ttl_seconds 는 양수여야 합니다")
        now = self.now()
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            if is_terminal(run.status):
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 run 에는 lease 를 획득할 수 없습니다: {run.status.value}",
                    details={"run_id": run_id},
                )
            row = conn.execute("SELECT * FROM leases WHERE run_id=?", (run_id,)).fetchone()
            takeover_from: str | None = None
            renewed = False
            if row is not None:
                holder = str(row["worker_id"])
                expires_at = float(row["expires_at"])
                if holder == self.worker_id:
                    renewed = True
                elif expires_at > now:
                    raise AgentError(
                        "E_LEASE_HELD",
                        f"run {run_id} 의 lease 를 다른 worker 가 보유 중입니다",
                        details={
                            "run_id": run_id,
                            "holder": holder,
                            "expires_in_seconds": round(expires_at - now, 3),
                        },
                    )
                else:
                    takeover_from = holder
            token = secrets.token_hex(8)
            expires = now + float(ttl_seconds)
            if row is None:
                conn.execute(
                    "INSERT INTO leases(run_id, worker_id, token, acquired_at, heartbeat_at, expires_at, ttl_seconds)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, self.worker_id, token, now, now, expires, float(ttl_seconds)),
                )
            else:
                conn.execute(
                    "UPDATE leases SET worker_id=?, token=?, acquired_at=?, heartbeat_at=?, expires_at=?, ttl_seconds=?"
                    " WHERE run_id=?",
                    (self.worker_id, token, now, now, expires, float(ttl_seconds), run_id),
                )
            conn.execute(
                "UPDATE runs SET worker_id=?, updated_at=? WHERE run_id=?",
                (self.worker_id, now_iso(), run_id),
            )
            if takeover_from is not None:
                self.record_event(
                    run_id,
                    "lease_takeover",
                    {
                        "from_worker": takeover_from,
                        "expired_at": float(row["expires_at"]),
                        "ttl_seconds": ttl_seconds,
                    },
                )
            else:
                self.record_event(
                    run_id, "lease_renewed" if renewed else "lease_acquired", {"ttl_seconds": ttl_seconds}
                )
            return Lease(
                run_id=run_id,
                worker_id=self.worker_id,
                token=token,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires,
                ttl_seconds=float(ttl_seconds),
            )

    def heartbeat(self, lease: Lease) -> Lease:
        """lease 연장. 다른 worker 가 인수했거나 token 이 다르면 E_LEASE_HELD (이 worker 는 작업을 중단해야 한다)."""
        if lease.worker_id != self.worker_id:
            raise AgentError(
                "E_LEASE_HELD", "다른 worker 의 lease 는 갱신할 수 없습니다", details={"run_id": lease.run_id}
            )
        now = self.now()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM leases WHERE run_id=?", (lease.run_id,)).fetchone()
            if row is None or str(row["worker_id"]) != lease.worker_id or str(row["token"]) != lease.token:
                raise AgentError(
                    "E_LEASE_HELD",
                    f"run {lease.run_id} 의 lease 를 잃었습니다 (만료 후 다른 worker 가 인수했거나 해제됨)",
                    details={
                        "run_id": lease.run_id,
                        "holder": str(row["worker_id"]) if row is not None else None,
                    },
                )
            late = float(row["expires_at"]) <= now
            expires = now + float(row["ttl_seconds"])
            conn.execute(
                "UPDATE leases SET heartbeat_at=?, expires_at=? WHERE run_id=?", (now, expires, lease.run_id)
            )
            if late:
                self.record_event(
                    lease.run_id,
                    "lease_late_heartbeat",
                    {"late_by_seconds": round(now - float(row["expires_at"]), 3)},
                )
            return lease.model_copy(update={"heartbeat_at": now, "expires_at": expires})

    def assert_lease_valid(self, lease: Lease) -> None:
        """epoch 경계 등에서 lease 소유를 확인한다. 잃었으면 E_LEASE_HELD."""
        row = self.db.query_one(
            "SELECT worker_id, token, expires_at FROM leases WHERE run_id=?", (lease.run_id,)
        )
        if row is None or str(row["worker_id"]) != lease.worker_id or str(row["token"]) != lease.token:
            raise AgentError(
                "E_LEASE_HELD", f"run {lease.run_id} 의 lease 를 잃었습니다", details={"run_id": lease.run_id}
            )

    def release(self, lease: Lease) -> bool:
        """자신의 lease 만 해제한다. 다른 worker 의 lease 이면 False."""
        if lease.worker_id != self.worker_id:
            return False
        with self.db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM leases WHERE run_id=? AND worker_id=? AND token=?",
                (lease.run_id, lease.worker_id, lease.token),
            )
            released = int(cur.rowcount or 0) > 0
            if released:
                conn.execute(
                    "UPDATE runs SET worker_id=NULL, updated_at=? WHERE run_id=? AND worker_id=?",
                    (now_iso(), lease.run_id, lease.worker_id),
                )
                self.record_event(lease.run_id, "lease_released", {})
            return released

    def expire_stale_leases(self, now: float | None = None) -> list[dict[str, Any]]:
        """만료된 lease 를 제거한다 (worker 무관 — 만료는 heartbeat 부재의 객관적 증거). 반환: 제거 목록."""
        ts = self.now() if now is None else float(now)
        removed: list[dict[str, Any]] = []
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM leases WHERE expires_at<=?", (ts,)).fetchall()
            for row in rows:
                conn.execute("DELETE FROM leases WHERE run_id=? AND token=?", (row["run_id"], row["token"]))
                info = {
                    "run_id": str(row["run_id"]),
                    "worker_id": str(row["worker_id"]),
                    "expired_at": float(row["expires_at"]),
                    "expired_by_seconds": round(ts - float(row["expires_at"]), 3),
                }
                removed.append(info)
                self.record_event(str(row["run_id"]), "lease_expired", info)
        return removed

    def cleanup_own(self, run_id: str | None = None) -> dict[str, Any]:
        """이 worker 가 남긴 lease/attempt 만 정리한다. 다른 worker 의 것은 건드리지 않는다."""
        ts = now_iso()
        with self.db.transaction() as conn:
            if run_id is None:
                lease_rows = conn.execute(
                    "SELECT * FROM leases WHERE worker_id=?", (self.worker_id,)
                ).fetchall()
                other = conn.execute(
                    "SELECT COUNT(*) FROM leases WHERE worker_id<>?", (self.worker_id,)
                ).fetchone()
            else:
                lease_rows = conn.execute(
                    "SELECT * FROM leases WHERE worker_id=? AND run_id=?", (self.worker_id, run_id)
                ).fetchall()
                other = conn.execute(
                    "SELECT COUNT(*) FROM leases WHERE worker_id<>? AND run_id=?", (self.worker_id, run_id)
                ).fetchone()
            released_runs: list[str] = []
            for row in lease_rows:
                conn.execute(
                    "DELETE FROM leases WHERE run_id=? AND worker_id=?", (row["run_id"], self.worker_id)
                )
                conn.execute(
                    "UPDATE runs SET worker_id=NULL, updated_at=? WHERE run_id=? AND worker_id=?",
                    (ts, row["run_id"], self.worker_id),
                )
                released_runs.append(str(row["run_id"]))
            if run_id is None:
                attempt_rows = conn.execute(
                    "SELECT a.attempt_id, a.trial_id FROM attempts a WHERE a.status=? AND a.worker_id=?",
                    (AttemptStatus.RUNNING.value, self.worker_id),
                ).fetchall()
            else:
                attempt_rows = conn.execute(
                    "SELECT a.attempt_id, a.trial_id FROM attempts a JOIN trials t ON t.trial_id=a.trial_id"
                    " WHERE a.status=? AND a.worker_id=? AND t.run_id=?",
                    (AttemptStatus.RUNNING.value, self.worker_id, run_id),
                ).fetchall()
            aborted: list[str] = []
            interrupted: list[str] = []
            for row in attempt_rows:
                conn.execute(
                    "UPDATE attempts SET status=?, reason=?, finished_at=? WHERE attempt_id=?",
                    (AttemptStatus.ABORTED.value, "worker cleanup", ts, row["attempt_id"]),
                )
                aborted.append(str(row["attempt_id"]))
                trow = conn.execute(
                    "SELECT status FROM trials WHERE trial_id=?", (row["trial_id"],)
                ).fetchone()
                if trow is not None and str(trow["status"]) == TrialStatus.RUNNING.value:
                    conn.execute(
                        "UPDATE trials SET status=?, reason=?, updated_at=? WHERE trial_id=?",
                        (TrialStatus.INTERRUPTED.value, "worker cleanup", ts, row["trial_id"]),
                    )
                    interrupted.append(str(row["trial_id"]))
            summary = {
                "worker_id": self.worker_id,
                "leases_released": released_runs,
                "attempts_aborted": aborted,
                "trials_interrupted": interrupted,
                "other_worker_leases_untouched": int(other[0]) if other is not None else 0,
            }
            for rid in released_runs or ([run_id] if run_id else []):
                self.record_event(rid, "worker_cleanup", summary)
            return summary

    # ------------------------------------------------------------------ trials / attempts
    def create_trial(
        self,
        run_id: str,
        trial_id: str,
        candidate_config: Mapping[str, Any],
        fingerprint: str,
    ) -> TrialRecord:
        if not trial_id or not trial_id.strip():
            raise AgentError("E_INPUT_INVALID", "trial_id 가 비어 있습니다")
        ts = now_iso()
        with self.db.transaction() as conn:
            run = self._get_run_locked(conn, run_id)
            if is_terminal(run.status):
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 run 에는 trial 을 만들 수 없습니다: {run.status.value}",
                    details={"run_id": run_id},
                )
            if conn.execute("SELECT 1 FROM trials WHERE trial_id=?", (trial_id,)).fetchone() is not None:
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"이미 존재하는 trial_id 입니다: {trial_id}",
                    details={"trial_id": trial_id},
                )
            conn.execute(
                "INSERT INTO trials(trial_id, run_id, fingerprint, status, candidate_json, metrics_json,"
                " artifact_hashes_json, worker_id, reason, created_at, updated_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, '{}', '{}', NULL, '', ?, ?, NULL)",
                (
                    trial_id,
                    run_id,
                    fingerprint,
                    TrialStatus.PENDING.value,
                    _dumps(dict(candidate_config)),
                    ts,
                    ts,
                ),
            )
            self.record_event(run_id, "trial_created", {"trial_id": trial_id, "fingerprint": fingerprint})
            return self._get_trial_locked(conn, trial_id)

    def _get_trial_locked(self, conn: Any, trial_id: str) -> TrialRecord:
        row = conn.execute("SELECT * FROM trials WHERE trial_id=?", (trial_id,)).fetchone()
        if row is None:
            raise AgentError(
                "E_INPUT_INVALID", f"trial 을 찾을 수 없습니다: {trial_id}", details={"trial_id": trial_id}
            )
        cnt = conn.execute("SELECT COUNT(*) FROM attempts WHERE trial_id=?", (trial_id,)).fetchone()
        return _row_to_trial(row, int(cnt[0]) if cnt is not None else 0)

    def get_trial(self, trial_id: str) -> TrialRecord:
        with self.db.transaction() as conn:
            return self._get_trial_locked(conn, trial_id)

    def list_trials(self, run_id: str) -> list[TrialRecord]:
        rows = self.db.query("SELECT * FROM trials WHERE run_id=? ORDER BY created_at, trial_id", (run_id,))
        out: list[TrialRecord] = []
        for row in rows:
            cnt = self.db.query_one("SELECT COUNT(*) FROM attempts WHERE trial_id=?", (row["trial_id"],))
            out.append(_row_to_trial(row, int(cnt[0]) if cnt is not None else 0))
        return out

    def create_attempt(self, trial_id: str, *, changes: Mapping[str, Any] | None = None) -> str:
        """새 attempt 생성. 같은 trial 의 열린 attempt 는 ABORTED(superseded) 로 닫는다. changes 에 microbatch 등 변경을 기록."""
        ts = now_iso()
        with self.db.transaction() as conn:
            trial = self._get_trial_locked(conn, trial_id)
            if trial.status in TERMINAL_TRIAL_STATUSES:
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 trial 에는 attempt 를 만들 수 없습니다: {trial.status.value}",
                    details={"trial_id": trial_id},
                )
            open_rows = conn.execute(
                "SELECT attempt_id FROM attempts WHERE trial_id=? AND status=?",
                (trial_id, AttemptStatus.RUNNING.value),
            ).fetchall()
            for row in open_rows:
                conn.execute(
                    "UPDATE attempts SET status=?, reason=?, finished_at=? WHERE attempt_id=?",
                    (AttemptStatus.ABORTED.value, "superseded by new attempt", ts, row["attempt_id"]),
                )
            attempt_no = trial.attempts + 1
            attempt_id = f"{trial_id}-a{attempt_no:02d}"
            conn.execute(
                "INSERT INTO attempts(attempt_id, trial_id, attempt_no, status, worker_id, changes_json, reason, started_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '', ?, NULL)",
                (
                    attempt_id,
                    trial_id,
                    attempt_no,
                    AttemptStatus.RUNNING.value,
                    self.worker_id,
                    _dumps(dict(changes or {})),
                    ts,
                ),
            )
            if trial.status is not TrialStatus.RUNNING:
                assert_trial_transition(trial.status, TrialStatus.RUNNING)
            conn.execute(
                "UPDATE trials SET status=?, worker_id=?, updated_at=? WHERE trial_id=?",
                (TrialStatus.RUNNING.value, self.worker_id, ts, trial_id),
            )
            self.record_event(
                trial.run_id,
                "attempt_created",
                {
                    "trial_id": trial_id,
                    "attempt_id": attempt_id,
                    "attempt_no": attempt_no,
                    "changes": dict(changes or {}),
                },
            )
            return attempt_id

    def get_attempt(self, attempt_id: str) -> AttemptRecord:
        row = self.db.query_one("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,))
        if row is None:
            raise AgentError(
                "E_INPUT_INVALID",
                f"attempt 를 찾을 수 없습니다: {attempt_id}",
                details={"attempt_id": attempt_id},
            )
        return _row_to_attempt(row)

    def list_attempts(self, trial_id: str) -> list[AttemptRecord]:
        rows = self.db.query("SELECT * FROM attempts WHERE trial_id=? ORDER BY attempt_no", (trial_id,))
        return [_row_to_attempt(r) for r in rows]

    def finish_attempt(self, attempt_id: str, status: AttemptStatus | str, reason: str = "") -> AttemptRecord:
        new = coerce_attempt_status(status)
        if new is AttemptStatus.RUNNING:
            raise AgentError("E_STATE_TRANSITION", "attempt 종료 상태는 RUNNING 일 수 없습니다")
        ts = now_iso()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise AgentError("E_INPUT_INVALID", f"attempt 를 찾을 수 없습니다: {attempt_id}")
            assert_attempt_transition(str(row["status"]), new)
            conn.execute(
                "UPDATE attempts SET status=?, reason=?, finished_at=? WHERE attempt_id=?",
                (new.value, reason, ts, attempt_id),
            )
            trow = conn.execute("SELECT run_id FROM trials WHERE trial_id=?", (row["trial_id"],)).fetchone()
            self.record_event(
                str(trow["run_id"]) if trow is not None else None,
                "attempt_finished",
                {
                    "attempt_id": attempt_id,
                    "trial_id": str(row["trial_id"]),
                    "status": new.value,
                    "reason": reason,
                },
            )
            return _row_to_attempt(
                conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            )

    def finish_trial(
        self,
        trial_id: str,
        status: TrialStatus | str,
        metrics: Mapping[str, Any] | None = None,
        artifact_hashes: Mapping[str, str] | None = None,
        reason: str = "",
    ) -> TrialRecord:
        """trial 을 종료 상태(COMPLETED/FAILED/CANCELLED/SKIPPED) 로 기록. 열린 attempt 는 함께 닫는다."""
        new = coerce_trial_status(status)
        if new not in TERMINAL_TRIAL_STATUSES:
            raise AgentError(
                "E_STATE_TRANSITION",
                f"finish_trial 은 종료 상태만 허용합니다: {new.value}",
                details={"allowed": sorted(s.value for s in TERMINAL_TRIAL_STATUSES)},
            )
        clean_metrics = _sanitize_metrics(metrics, strict_finite=new is TrialStatus.COMPLETED)
        hashes = {str(k): str(v) for k, v in (artifact_hashes or {}).items()}
        ts = now_iso()
        with self.db.transaction() as conn:
            trial = self._get_trial_locked(conn, trial_id)
            assert_trial_transition(trial.status, new)
            attempt_status = {
                TrialStatus.COMPLETED: AttemptStatus.COMPLETED,
                TrialStatus.FAILED: AttemptStatus.FAILED,
            }.get(new, AttemptStatus.ABORTED)
            conn.execute(
                "UPDATE attempts SET status=?, reason=?, finished_at=? WHERE trial_id=? AND status=?",
                (
                    attempt_status.value,
                    reason or f"trial {new.value.lower()}",
                    ts,
                    trial_id,
                    AttemptStatus.RUNNING.value,
                ),
            )
            conn.execute(
                "UPDATE trials SET status=?, metrics_json=?, artifact_hashes_json=?, reason=?, updated_at=?, finished_at=?"
                " WHERE trial_id=?",
                (new.value, _dumps(clean_metrics), _dumps(hashes), reason, ts, ts, trial_id),
            )
            self.record_event(
                trial.run_id,
                "trial_finished",
                {
                    "trial_id": trial_id,
                    "status": new.value,
                    "metrics": clean_metrics,
                    "artifacts": sorted(hashes),
                    "reason": reason,
                },
            )
            return self._get_trial_locked(conn, trial_id)

    def find_completed_trial(self, fingerprint: str, *, run_id: str | None = None) -> TrialRecord | None:
        """동일 fingerprint 의 가장 최근 COMPLETED trial. 산출물 검증은 is_trial_reusable 로 별도 수행한다."""
        if run_id is None:
            row = self.db.query_one(
                "SELECT * FROM trials WHERE fingerprint=? AND status=? ORDER BY finished_at DESC, created_at DESC LIMIT 1",
                (fingerprint, TrialStatus.COMPLETED.value),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM trials WHERE fingerprint=? AND status=? AND run_id=?"
                " ORDER BY finished_at DESC, created_at DESC LIMIT 1",
                (fingerprint, TrialStatus.COMPLETED.value, run_id),
            )
        if row is None:
            return None
        cnt = self.db.query_one("SELECT COUNT(*) FROM attempts WHERE trial_id=?", (row["trial_id"],))
        return _row_to_trial(row, int(cnt[0]) if cnt is not None else 0)

    def verify_trial_artifacts(
        self, trial: TrialRecord, artifact_dir: str | os.PathLike[str]
    ) -> dict[str, str]:
        """artifact_hashes 의 각 파일을 artifact_dir 아래에서 sha256 으로 검증. 값: ok/missing/mismatch/outside."""
        base = Path(artifact_dir).resolve()
        result: dict[str, str] = {}
        for rel, expected in trial.artifact_hashes.items():
            target = (base / rel).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                result[rel] = "outside"
                continue
            if not target.is_file():
                result[rel] = "missing"
                continue
            result[rel] = "ok" if sha256_file(target) == expected else "mismatch"
        return result

    def is_trial_reusable(self, trial: TrialRecord | None, artifact_dir: str | os.PathLike[str]) -> bool:
        """완료 trial 이고 산출물 hash 가 모두 일치할 때만 True. 산출물 기록이 없으면 검증 불가 → False (재실행)."""
        if trial is None or trial.status is not TrialStatus.COMPLETED or not trial.artifact_hashes:
            return False
        checks = self.verify_trial_artifacts(trial, artifact_dir)
        return bool(checks) and all(v == "ok" for v in checks.values())

    def mark_trial_skipped(self, trial_id: str, reused: TrialRecord) -> TrialRecord:
        """검증된 완료 trial 을 재사용하여 새 trial 을 SKIPPED 로 기록 (metrics/artifact hash 복사, 출처 명시)."""
        return self.finish_trial(
            trial_id,
            TrialStatus.SKIPPED,
            metrics=reused.metrics,
            artifact_hashes=reused.artifact_hashes,
            reason=f"reused:{reused.trial_id}",
        )
