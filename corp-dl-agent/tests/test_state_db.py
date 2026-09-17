"""StateDB 시험: WAL/한글 경로, schema_version, transaction, 동시 writer, backup API."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from corp_dl_agent.common import sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.state.db import MIGRATIONS, StateDB
from corp_dl_agent.version import DB_SCHEMA_VERSION

_INSERT_RUN = (
    "INSERT INTO runs(run_id, kind, status, task_fingerprint, config_hash, metadata_json, created_at, updated_at)"
    " VALUES (?, 'k', 'CREATED', 'fp', 'cfg', ?, 't0', 't0')"
)


def _count_runs(db: StateDB) -> int:
    row = db.query_one("SELECT COUNT(*) FROM runs")
    assert row is not None
    return int(row[0])


def test_creates_wal_db_in_korean_path(tmp_path: Path) -> None:
    p = tmp_path / "한글 폴더 이름" / "상태 DB.sqlite"
    with StateDB(p) as db:
        assert p.is_file()
        assert db.journal_mode == "wal"
        assert db.schema_version() == DB_SCHEMA_VERSION
        assert db.migration_plan()["action"] == "none"
        assert db.integrity_check() == "ok"
        tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"schema_version", "runs", "leases", "trials", "attempts", "events"} <= tables
    assert db.closed


def test_migrations_target_matches_version_and_reopen_is_idempotent(tmp_path: Path) -> None:
    assert max(MIGRATIONS) == DB_SCHEMA_VERSION
    p = tmp_path / "s.sqlite"
    db = StateDB(p)
    db.execute(_INSERT_RUN, ("r1", "{}"))
    db.close()
    db2 = StateDB(p)
    assert db2.schema_version() == DB_SCHEMA_VERSION
    assert _count_runs(db2) == 1
    rows = db2.query("SELECT version, app_version FROM schema_version")
    assert [int(r["version"]) for r in rows] == [DB_SCHEMA_VERSION]
    plan = db2.migrate()
    assert plan["action"] == "none" and "applied" not in plan
    db2.close()


def test_transaction_commit_and_rollback(tmp_path: Path) -> None:
    with StateDB(tmp_path / "s.sqlite") as db:
        with db.transaction() as conn:
            conn.execute(_INSERT_RUN, ("r1", "{}"))
            assert db.in_transaction
        assert not db.in_transaction
        assert _count_runs(db) == 1
        with pytest.raises(RuntimeError):
            with db.transaction() as conn:
                conn.execute(_INSERT_RUN, ("r2", "{}"))
                raise RuntimeError("boom")
        assert not db.in_transaction
        assert _count_runs(db) == 1  # rolled back
        db.execute(_INSERT_RUN, ("r3", "{}"))  # transaction 밖 execute 는 자체 transaction
        assert _count_runs(db) == 2


def test_nested_transaction_commits_once_and_inner_failure_rolls_back_all(tmp_path: Path) -> None:
    with StateDB(tmp_path / "s.sqlite") as db:
        with db.transaction() as conn:
            conn.execute(_INSERT_RUN, ("a", "{}"))
            with db.transaction() as inner:
                inner.execute(_INSERT_RUN, ("b", "{}"))
                assert db.in_transaction
            assert db.in_transaction  # 안쪽이 끝나도 바깥 transaction 은 열려 있다
        assert _count_runs(db) == 2
        with pytest.raises(ValueError):
            with db.transaction() as conn:
                conn.execute(_INSERT_RUN, ("c", "{}"))
                with db.transaction() as inner:
                    inner.execute(_INSERT_RUN, ("d", "{}"))
                    raise ValueError("inner")
        assert _count_runs(db) == 2  # c, d 모두 취소


def test_concurrent_writers_from_two_connections_do_not_lose_updates(tmp_path: Path) -> None:
    p = tmp_path / "동시.sqlite"
    with StateDB(p) as setup:
        setup.execute(_INSERT_RUN, ("counter", json.dumps({"n": 0})))
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            db = StateDB(p, busy_timeout_ms=10000)
            try:
                for _ in range(n):
                    with db.transaction() as conn:  # BEGIN IMMEDIATE 가 read-modify-write 를 직렬화
                        row = conn.execute("SELECT metadata_json FROM runs WHERE run_id='counter'").fetchone()
                        value = json.loads(row[0])["n"] + 1
                        conn.execute(
                            "UPDATE runs SET metadata_json=? WHERE run_id='counter'",
                            (json.dumps({"n": value}),),
                        )
            finally:
                db.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(25,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    with StateDB(p) as db:
        row = db.query_one("SELECT metadata_json FROM runs WHERE run_id='counter'")
        assert row is not None and json.loads(row[0])["n"] == 50


def test_second_connection_gets_e_db_locked_while_writer_holds_lock(tmp_path: Path) -> None:
    p = tmp_path / "lock.sqlite"
    a = StateDB(p)
    b = StateDB(p, busy_timeout_ms=100)
    try:
        with a.transaction() as conn:
            conn.execute(_INSERT_RUN, ("held", "{}"))
            with pytest.raises(AgentError) as ei:
                with b.transaction():
                    pass
            assert ei.value.code == "E_DB_LOCKED"
            assert ei.value.details["busy_timeout_ms"] == 100
            assert not b.in_transaction
        # writer 가 commit 한 뒤에는 두 번째 connection 이 쓸 수 있고 commit 된 행을 본다
        with b.transaction() as conn:
            conn.execute(_INSERT_RUN, ("after", "{}"))
        assert _count_runs(b) == 2
    finally:
        a.close()
        b.close()


def test_backup_to_makes_consistent_copy_including_wal_content(tmp_path: Path) -> None:
    src_path = tmp_path / "원본 폴더" / "state.sqlite"
    db = StateDB(src_path)
    try:
        db.query("PRAGMA wal_autocheckpoint=0")  # 자동 checkpoint 를 끄고 변경이 WAL 에만 있게 만든다
        for i in range(5):
            db.execute(_INSERT_RUN, (f"r{i}", "{}"))
        wal = src_path.with_name(src_path.name + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        dest = tmp_path / "백업 폴더" / "state 백업.sqlite"
        info = db.backup_to(dest)
        assert info["integrity"] == "ok"
        assert info["method"] == "sqlite3.Connection.backup"
        assert info["runs"] == 5 and info["schema_version"] == DB_SCHEMA_VERSION
        assert info["sha256"] == sha256_file(dest)
        assert not [p for p in dest.parent.iterdir() if p.name.startswith(".")]  # temp 파일 잔존 없음
        with StateDB(dest) as copy:
            assert _count_runs(copy) == 5
            assert copy.schema_version() == DB_SCHEMA_VERSION
            assert copy.integrity_check() == "ok"
        # 대조: WAL 사용 중 DB 파일 하나만 복사하면 WAL 의 변경이 빠진다 → 파일 복사는 백업이 아니다
        raw = tmp_path / "raw_copy.sqlite"
        raw.write_bytes(src_path.read_bytes())
        conn = sqlite3.connect(str(raw))
        try:
            raw_count = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        except sqlite3.OperationalError:
            raw_count = -1
        finally:
            conn.close()
        assert raw_count < 5
        # 두 번째 backup 은 기존 사본을 원자적으로 교체하고 새 행을 반영한다
        db.execute(_INSERT_RUN, ("r5", "{}"))
        info2 = db.backup_to(dest)
        assert info2["runs"] == 6 and info2["sha256"] != info["sha256"]
    finally:
        db.close()


def test_backup_refused_inside_transaction(tmp_path: Path) -> None:
    with StateDB(tmp_path / "s.sqlite") as db:
        with db.transaction():
            with pytest.raises(AgentError) as ei:
                db.backup_to(tmp_path / "b.sqlite")
            assert ei.value.code == "E_INTERNAL"
        assert not (tmp_path / "b.sqlite").exists()


def test_newer_schema_version_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "s.sqlite"
    StateDB(p).close()
    conn = sqlite3.connect(str(p))
    conn.execute(
        "INSERT INTO schema_version(version, applied_at, app_version) VALUES (?, 't', 'future')",
        (DB_SCHEMA_VERSION + 1,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AgentError) as ei:
        StateDB(p)
    assert ei.value.code == "E_ROLLBACK_INCOMPATIBLE"
    assert ei.value.details["current"] == DB_SCHEMA_VERSION + 1
    plan = StateDB(p, auto_migrate=False)
    try:
        assert plan.migration_plan()["action"] == "incompatible_newer"
    finally:
        plan.close()


def test_unknown_legacy_schema_version_has_no_migration_path(tmp_path: Path) -> None:
    p = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, app_version TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO schema_version VALUES (2, 't', '2.0.0')")
    conn.commit()
    conn.close()
    db = StateDB(p, auto_migrate=False)
    try:
        assert db.migration_plan()["action"] == "incompatible_unknown"
        with pytest.raises(AgentError) as ei:
            db.migrate()
        assert ei.value.code == "E_ROLLBACK_INCOMPATIBLE"
    finally:
        db.close()


def test_fresh_migration_plan_and_apply(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "s.sqlite", auto_migrate=False)
    try:
        assert db.schema_version() == 0
        plan = db.migration_plan()
        assert plan["action"] == "migrate" and plan["steps"] == [DB_SCHEMA_VERSION]
        applied = db.migrate()
        assert applied["applied"] == [DB_SCHEMA_VERSION] and applied["current"] == DB_SCHEMA_VERSION
    finally:
        db.close()


def test_memory_and_closed_db_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AgentError) as ei:
        StateDB(":memory:")
    assert ei.value.code == "E_INPUT_INVALID"
    db = StateDB(tmp_path / "s.sqlite")
    db.close()
    db.close()  # 두 번 닫아도 안전
    with pytest.raises(AgentError) as ei2:
        db.query("SELECT 1")
    assert ei2.value.code == "E_INTERNAL"


def test_checkpoint_returns_counts(tmp_path: Path) -> None:
    with StateDB(tmp_path / "s.sqlite") as db:
        db.execute(_INSERT_RUN, ("r", "{}"))
        result = db.checkpoint()
        assert set(result) == {"busy", "log", "checkpointed"}
        assert result["busy"] == 0
