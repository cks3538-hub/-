"""로컬 SQLite 상태 DB.

규칙 (BUILD_SPEC [6], [9], ARCHITECTURE 261행):
- 로컬 디스크 파일. WAL journal + busy_timeout. 네트워크 공유 경로를 기본값으로 쓰지 않는다.
- 단일 writer: 모든 쓰기는 transaction() 안에서 BEGIN IMMEDIATE 로 수행한다. 다른 connection 이 쓰기 잠금을
  잡고 있으면 busy_timeout 까지 기다린 뒤 E_DB_LOCKED.
- schema_version 테이블로 version.DB_SCHEMA_VERSION 과 비교한다. DB 가 더 새로우면 E_ROLLBACK_INCOMPATIBLE.
- backup_to() 는 sqlite3 backup API 를 사용한다. WAL 사용 중 DB 파일 하나만 복사하면 WAL 에 있는 변경이 빠지므로
  파일 복사를 백업으로 간주하지 않는다.
- 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from corp_dl_agent.common import now_iso, sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import DB_SCHEMA_VERSION, __version__

log = logging.getLogger("corp_dl_agent.state.db")

# schema version 4 (v4 최초 schema). 이전 v1~v3 schema 는 알려져 있지 않으므로 migration 경로가 없다.
SCHEMA_V4: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        previous_status TEXT,
        task_fingerprint TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        worker_id TEXT,
        pause_requested INTEGER NOT NULL DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    """CREATE TABLE IF NOT EXISTS leases (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
        worker_id TEXT NOT NULL,
        token TEXT NOT NULL,
        acquired_at REAL NOT NULL,
        heartbeat_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        ttl_seconds REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_leases_worker ON leases(worker_id)",
    """CREATE TABLE IF NOT EXISTS trials (
        trial_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        fingerprint TEXT NOT NULL,
        status TEXT NOT NULL,
        candidate_json TEXT NOT NULL DEFAULT '{}',
        metrics_json TEXT NOT NULL DEFAULT '{}',
        artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
        worker_id TEXT,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trials_fingerprint ON trials(fingerprint, status)",
    "CREATE INDEX IF NOT EXISTS idx_trials_run ON trials(run_id)",
    """CREATE TABLE IF NOT EXISTS attempts (
        attempt_id TEXT PRIMARY KEY,
        trial_id TEXT NOT NULL REFERENCES trials(trial_id),
        attempt_no INTEGER NOT NULL,
        status TEXT NOT NULL,
        worker_id TEXT,
        changes_json TEXT NOT NULL DEFAULT '{}',
        reason TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_attempts_trial ON attempts(trial_id, attempt_no)",
    """CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        ts TEXT NOT NULL,
        event TEXT NOT NULL,
        worker_id TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id)",
)

# 적용 순서대로의 migration. key 는 도달하는 schema version.
MIGRATIONS: dict[int, tuple[str, ...]] = {4: SCHEMA_V4}

_SCHEMA_VERSION_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_version ("
    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, app_version TEXT NOT NULL)"
)


def _is_locked_error(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


class StateDB:
    """sqlite3 connection 래퍼. 한 프로세스 안에서는 RLock 으로, 프로세스 간에는 BEGIN IMMEDIATE 로 단일 writer 를 보장한다."""

    def __init__(
        self, path: str | os.PathLike[str], *, busy_timeout_ms: int = 5000, auto_migrate: bool = True
    ) -> None:
        self.path = Path(path)
        if str(self.path) == ":memory:":
            raise AgentError("E_INPUT_INVALID", "상태 DB 는 로컬 디스크 파일이어야 합니다 (:memory: 불가)")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._tx_failed = False
        self._closed = False
        try:
            self._conn = sqlite3.connect(
                str(self.path),
                timeout=self.busy_timeout_ms / 1000.0,
                isolation_level=None,  # 자동 BEGIN 없음: transaction() 이 BEGIN IMMEDIATE 를 직접 실행한다
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise AgentError(
                "E_INTERNAL",
                f"상태 DB 를 열 수 없습니다: {self.path.name}",
                details={"error": str(exc)[:300]},
            ) from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        row = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()
        self.journal_mode = str(row[0]).lower() if row is not None else "unknown"
        if self.journal_mode != "wal":
            log.warning(
                "상태 DB 가 WAL 모드가 아닙니다 (journal_mode=%s). 로컬 디스크 경로를 사용하세요.",
                self.journal_mode,
            )
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if auto_migrate:
            self.migrate()

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._tx_depth > 0:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                self._tx_depth = 0
            try:
                self._conn.close()
            finally:
                self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise AgentError("E_INTERNAL", "닫힌 상태 DB 에 접근했습니다")

    # ------------------------------------------------------------------ transaction
    def _begin_immediate(self) -> None:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_locked_error(exc):
                raise AgentError(
                    "E_DB_LOCKED",
                    f"상태 DB 쓰기 잠금을 {self.busy_timeout_ms}ms 안에 얻지 못했습니다: {self.path.name}",
                    details={"busy_timeout_ms": self.busy_timeout_ms},
                ) from exc
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK. 중첩 시 가장 바깥 transaction 만 실제 BEGIN/COMMIT 을 수행한다."""
        self._require_open()
        with self._lock:
            outermost = self._tx_depth == 0
            if outermost:
                self._tx_failed = False
                self._begin_immediate()
            self._tx_depth += 1
            try:
                yield self._conn
            except BaseException:
                self._tx_failed = True
                raise
            finally:
                self._tx_depth -= 1
                if outermost:
                    self._tx_depth = 0
                    if self._tx_failed:
                        try:
                            self._conn.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
                    else:
                        try:
                            self._conn.execute("COMMIT")
                        except sqlite3.Error:
                            try:
                                self._conn.execute("ROLLBACK")
                            except sqlite3.Error:
                                pass
                            raise

    @property
    def in_transaction(self) -> bool:
        return self._tx_depth > 0

    # ------------------------------------------------------------------ helpers
    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        """쓰기 문장 실행. transaction 밖에서 호출되면 자체 transaction 으로 감싼다."""
        self._require_open()
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        self._require_open()
        with self.transaction() as conn:
            conn.executemany(sql, seq)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        """읽기. WAL 모드에서는 다른 writer 를 막지 않는다."""
        self._require_open()
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        self._require_open()
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row if row is not None else None

    # ------------------------------------------------------------------ schema
    def _has_table(self, name: str) -> bool:
        row = self.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return row is not None

    def schema_version(self) -> int:
        if not self._has_table("schema_version"):
            return 0
        row = self.query_one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"]) if row is not None and row["v"] is not None else 0

    def migration_plan(self) -> dict[str, Any]:
        """현재 version 과 목표 version, 적용할 단계. upgrade 스크립트가 백업 뒤 계획을 보여주는 데 사용한다."""
        current = self.schema_version()
        target = DB_SCHEMA_VERSION
        known = sorted(MIGRATIONS)
        if current > target:
            action = "incompatible_newer"
            steps: list[int] = []
        elif current == target:
            action = "none"
            steps = []
        elif current != 0 and current not in known:
            action = "incompatible_unknown"
            steps = []
        else:
            action = "migrate"
            steps = [v for v in known if current < v <= target]
        return {
            "path": str(self.path),
            "current": current,
            "target": target,
            "steps": steps,
            "action": action,
            "app_version": __version__,
        }

    def migrate(self) -> dict[str, Any]:
        """schema 를 DB_SCHEMA_VERSION 까지 올린다. 멱등이며 BEGIN IMMEDIATE 안에서 version 을 재확인한다."""
        if max(MIGRATIONS) != DB_SCHEMA_VERSION:
            raise AgentError(
                "E_INTERNAL",
                "MIGRATIONS 와 version.DB_SCHEMA_VERSION 이 일치하지 않습니다",
                details={"migrations": sorted(MIGRATIONS), "db_schema_version": DB_SCHEMA_VERSION},
            )
        plan = self.migration_plan()
        if plan["action"] == "incompatible_newer":
            raise AgentError(
                "E_ROLLBACK_INCOMPATIBLE",
                f"상태 DB schema version {plan['current']} 이(가) 이 프로그램의 {plan['target']} 보다 새롭습니다",
                details={"path": str(self.path), "current": plan["current"], "target": plan["target"]},
            )
        if plan["action"] == "incompatible_unknown":
            raise AgentError(
                "E_ROLLBACK_INCOMPATIBLE",
                f"상태 DB schema version {plan['current']} 에서 {plan['target']} 로 가는 migration 경로가 없습니다",
                details={"path": str(self.path), "current": plan["current"], "target": plan["target"]},
            )
        if plan["action"] == "none":
            return plan
        applied: list[int] = []
        with self.transaction() as conn:
            conn.execute(_SCHEMA_VERSION_DDL)
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int(row["v"]) if row is not None and row["v"] is not None else 0
            for version in sorted(MIGRATIONS):
                if version <= current or version > DB_SCHEMA_VERSION:
                    continue
                for stmt in MIGRATIONS[version]:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at, app_version) VALUES (?, ?, ?)",
                    (version, now_iso(), __version__),
                )
                applied.append(version)
        plan["applied"] = applied
        plan["current"] = self.schema_version()
        return plan

    # ------------------------------------------------------------------ maintenance
    def integrity_check(self) -> str:
        row = self.query_one("PRAGMA integrity_check")
        return str(row[0]) if row is not None else "unknown"

    def checkpoint(self) -> dict[str, int]:
        """WAL 내용을 본 파일로 반영 (upgrade/backup 전 정리용). 다른 reader 가 있으면 일부만 반영될 수 있다."""
        self._require_open()
        with self._lock:
            row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is None:
            return {"busy": -1, "log": -1, "checkpointed": -1}
        return {"busy": int(row[0]), "log": int(row[1]), "checkpointed": int(row[2])}

    def backup_to(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """sqlite3 backup API 로 일관된 사본을 만든다 (WAL 내용 포함).

        temp 파일에 backup -> integrity_check -> os.replace. 실패하면 temp 를 제거하고 기존 사본은 유지한다.
        """
        self._require_open()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.backup-tmp")
        try:
            if tmp.exists():
                tmp.unlink()
            dst = sqlite3.connect(str(tmp))
            try:
                with self._lock:
                    if self._tx_depth > 0:
                        raise AgentError("E_INTERNAL", "transaction 안에서는 backup_to 를 호출할 수 없습니다")
                    self._conn.backup(dst, pages=0)
            finally:
                dst.close()
            check = sqlite3.connect(str(tmp))
            try:
                integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
                vrow = check.execute("SELECT MAX(version) FROM schema_version").fetchone()
                version = int(vrow[0]) if vrow is not None and vrow[0] is not None else 0
                nrows = int(check.execute("SELECT COUNT(*) FROM runs").fetchone()[0]) if version else 0
            finally:
                check.close()
            if integrity != "ok":
                raise AgentError(
                    "E_CHECKPOINT_CORRUPT",
                    "백업 사본 integrity_check 실패",
                    details={"result": integrity[:200]},
                )
            os.replace(tmp, dest)
        except BaseException:
            for leftover in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass
            raise
        return {
            "path": str(dest),
            "sha256": sha256_file(dest),
            "size_bytes": dest.stat().st_size,
            "schema_version": version,
            "runs": nrows,
            "integrity": integrity,
            "method": "sqlite3.Connection.backup",
            "created_at": now_iso(),
            "source": str(self.path),
        }
