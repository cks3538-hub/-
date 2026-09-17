"""install/launch/selftest/upgrade/rollback/acceptance 스크립트 통합 시험 (실제 venv 생성, 네트워크 없음).

- 한글·공백 경로 install_root/data_root 에 host Python(base) 으로 venv 를 만들고 --no-index --require-hashes 로 설치한다.
- venv 생성은 비용이 크므로 module 단위 fixture 로 1.0.0 → (upgrade) 1.1.0 → (실패 upgrade) 1.2.0 의 3회로 제한한다.
- 다른 CWD 에서 실행, 동일 release idempotent, hash 불일치 시 active 유지, 회사 설정/DB 보존, lease 잠금, DB rollback 분리.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.packaging import verifier_core as core
from tests.test_packaging_kit import build_fake_release, host_profile, read_json, run_script, script_env

pytestmark = pytest.mark.slow


def _base_python() -> str:
    py = core.default_base_python()
    probe = core.probe_python(py)
    if not probe or not probe.get("has_venv") or not probe.get("has_ensurepip"):
        pytest.skip(f"venv/ensurepip 가 있는 base Python 이 없음: {py}")
    return py


def _install_env(**extra: str) -> dict[str, str]:
    """pip 환경변수/프록시가 설정되어 있어도 설치기가 차단하는지 함께 확인하기 위한 오염된 환경."""
    env = script_env(**extra)
    env["PIP_INDEX_URL"] = "http://127.0.0.1:9/simple"
    env["PIP_EXTRA_INDEX_URL"] = "http://127.0.0.1:9/extra"
    env["PIP_FIND_LINKS"] = "/nonexistent/wheels"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["PIP_NO_INDEX"] = "0"
    return env


def _make_state_db(path: Path) -> None:
    """앱과 같은 최소 schema(schema_version/runs/leases) 의 상태 DB (WAL)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, app_version TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leases (run_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, token TEXT NOT NULL, acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL, ttl_seconds REAL NOT NULL)"
        )
        conn.execute("INSERT OR IGNORE INTO schema_version VALUES (4, '2026-01-01T00:00:00+00:00', 'test')")
        conn.execute("INSERT OR IGNORE INTO runs VALUES ('run-synthetic-1', 'train', 'COMPLETED')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _db_exec(path: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(sql, params)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.profile = host_profile()
        self.base_python = _base_python()
        self.install_root = tmp / "설치 root 한글 공백" / "DIA"
        self.data_root = tmp / "데이터 root" / "workspace"
        self.other_cwd = tmp / "다른 작업 폴더"
        self.other_cwd.mkdir(parents=True)
        self.v100 = build_fake_release(tmp, version="1.0.0")
        self.v110 = build_fake_release(tmp, version="1.1.0")
        self.v120_bad = build_fake_release(tmp, version="1.2.0", selftest_ok=False)
        self.v100_other = build_fake_release(
            tmp, version="1.0.0", label="-other", extra_files={"docs/EXTRA.md": "different content\n"}
        )

    def release_dir(self, version: str) -> Path:
        return self.install_root / "releases" / version / self.profile.profile_id

    def venv_python(self, version: str) -> Path:
        return core.venv_python_path(self.release_dir(version) / ".venv")

    def active(self) -> dict[str, Any]:
        return read_json(self.install_root / "active.json")

    def active_bytes(self) -> bytes:
        return (self.install_root / "active.json").read_bytes()

    def db_path(self) -> Path:
        return self.data_root / "state" / "agent_state.sqlite"

    def backups(self) -> list[Path]:
        b = self.install_root / "backups"
        return sorted(p for p in b.iterdir() if p.is_dir()) if b.is_dir() else []

    def run(
        self,
        script: str,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 240,
    ) -> subprocess.CompletedProcess[str]:
        return run_script(
            script, *args, cwd=cwd or self.other_cwd, env=env or _install_env(), timeout=timeout
        )

    def run_json(self, script: str, *args: str, **kw: Any) -> tuple[int, dict[str, Any]]:
        r = self.run(script, *args, "--json", **kw)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {"raw_stdout": r.stdout, "stderr": r.stderr}
        return r.returncode, out


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Env:
    e = Env(tmp_path_factory.mktemp("install"))
    rc, out = e.run_json(
        "install.py",
        "--package",
        str(e.v100.zip_path),
        "--install-root",
        str(e.install_root),
        "--data-root",
        str(e.data_root),
        "--python",
        e.base_python,
        "--skip-selftest",
    )
    assert rc == 0, out
    assert out["ok"] is True and out["activated"] is True and out["reused"] is False
    return e


# --------------------------------------------------------------------------- install
def test_install_creates_venv_in_final_location_and_active_json(env: Env) -> None:
    rd = env.release_dir("1.0.0")
    venv_py = env.venv_python("1.0.0")
    assert (
        venv_py.is_file()
        and (rd / "release-manifest.json").is_file()
        and (rd / "scripts" / "install.py").is_file()
    )
    active = env.active()
    assert active["release_id"] == "1.0.0" and active["profile_id"] == env.profile.profile_id
    assert Path(active["venv_python"]) == venv_py and Path(active["release_dir"]) == rd
    assert Path(active["data_root"]) == env.data_root and active["activated_by"] == "install"
    assert active["app_module"] == "fake_app" and active["previous"] is None
    log = read_json(rd / "install_log.json")
    assert (
        log["status"] == "installed"
        and log["skip_selftest"] is True
        and log["base_python"] == env.base_python
    )
    steps = {s["name"]: s for s in log["steps"]}
    assert steps["selftest"]["status"] == "skipped"
    pip_cmd = steps["pip_install"]["command"]
    for flag in (
        "--isolated",
        "--no-index",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
    ):
        assert flag in pip_cmd, flag
    assert steps["pip_check"]["exit_code"] == 0 and steps["import_app"]["exit_code"] == 0
    assert steps["smoke_version_match"]["reported"] == "1.0.0"
    # 설치된 venv 에서 앱/의존성 import (네트워크 없이 wheelhouse 로만 설치됨)
    r = subprocess.run(
        [
            str(venv_py),
            "-c",
            "import fake_app, fake_dep, fake_bin; print(fake_app.__version__, fake_dep.VALUE, fake_bin.VALUE)",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=core.sanitized_env(),
        timeout=60,
    )
    assert r.returncode == 0 and r.stdout.split() == ["1.0.0", "1", "2"], r.stderr
    # 폴더 구조 (company/workspace/backups) 는 만들되 내용은 건드리지 않음
    for sub in core.COMPANY_SUBDIRS:
        assert (env.install_root / "company" / sub).is_dir()
    for sub in core.WORKSPACE_SUBDIRS:
        assert (env.data_root / sub).is_dir()
    assert (env.install_root / "backups").is_dir()


def test_launch_from_other_cwd_uses_active_venv(env: Env) -> None:
    r = env.run(
        "launch.py", "--install-root", str(env.install_root), "--", "echo", "a", "b c", cwd=env.other_cwd
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["args"] == ["a", "b c"] and out["env_install_root"] == str(env.install_root)
    # DIA_INSTALL_ROOT 환경변수만으로도 동작
    r = env.run("launch.py", "echo", "x", env=_install_env(DIA_INSTALL_ROOT=str(env.install_root)))
    assert r.returncode == 0 and json.loads(r.stdout.strip().splitlines()[-1])["args"] == ["x"]
    # active 없음 → E_BLOCKED_CONFIG (exit 3) 안내
    r = env.run("launch.py", "--install-root", str(env.tmp / "없는 설치"), "--", "echo")
    assert r.returncode == core.EXIT_BLOCKED_CONFIG and "active.json" in r.stderr


def test_selftest_wrapper_writes_log(env: Env) -> None:
    r = env.run("selftest.py", "--install-root", str(env.install_root), "--json")
    assert r.returncode == 0, r.stderr
    logs = sorted((env.data_root / "logs").glob("selftest_*.json"))
    assert logs and read_json(logs[-1])["ok"] is True


def test_reinstall_same_release_is_idempotent(env: Env) -> None:
    before = env.active_bytes()
    venv_mtime = env.venv_python("1.0.0").stat().st_mtime
    rc, out = env.run_json(
        "install.py",
        "--package",
        str(env.v100.zip_path),
        "--install-root",
        str(env.install_root),
        "--data-root",
        str(env.data_root),
        "--python",
        env.base_python,
    )
    assert rc == 0 and out["reused"] is True and out["activated"] is True, out
    assert env.active_bytes() == before, "동일 release 재설치는 active.json 을 다시 쓰지 않는다"
    assert env.venv_python("1.0.0").stat().st_mtime == venv_mtime
    assert [p.name for p in (env.install_root / "releases").iterdir()] == ["1.0.0"]


def test_install_tampered_package_keeps_active(env: Env, tmp_path: Path) -> None:
    import zipfile

    bad = tmp_path / "tampered.zip"
    with zipfile.ZipFile(env.v110.zip_path) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            zout.writestr(info.filename, data + (b"\n#x" if info.filename == "scripts/verify.py" else b""))
    before = env.active_bytes()
    rc, out = env.run_json(
        "install.py",
        "--package",
        str(bad),
        "--install-root",
        str(env.install_root),
        "--data-root",
        str(env.data_root),
        "--python",
        env.base_python,
    )
    assert rc == core.EXIT_VALIDATION and out["error"]["code"] == "E_PACKAGE_INVALID"
    assert "file_hashes" in out["error"]["message"]
    assert env.active_bytes() == before and not env.release_dir("1.1.0").exists()
    r = env.run(
        "install.py",
        "--package",
        str(bad),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert r.returncode == core.EXIT_VALIDATION and "기존 active.json 은 변경되지 않았습니다" in r.stderr


def test_install_same_release_id_different_content_rejected(env: Env) -> None:
    before = env.active_bytes()
    rc, out = env.run_json(
        "install.py",
        "--package",
        str(env.v100_other.zip_path),
        "--install-root",
        str(env.install_root),
        "--data-root",
        str(env.data_root),
        "--python",
        env.base_python,
    )
    assert (
        rc == core.EXIT_RUNTIME
        and out["error"]["code"] == "E_INSTALL_FAILED"
        and "다른 내용" in out["error"]["message"]
    )
    assert env.active_bytes() == before


def test_install_missing_python_is_blocked_prerequisite(env: Env) -> None:
    rc, out = env.run_json(
        "install.py",
        "--package",
        str(env.v110.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        str(env.tmp / "없는 python"),
    )
    assert rc == core.EXIT_BLOCKED_PREREQUISITE and out["error"]["code"] == "E_BLOCKED_PREREQUISITE"
    assert not env.release_dir("1.1.0").exists()


# --------------------------------------------------------------------------- upgrade
def test_upgrade_blocked_by_active_lease(env: Env) -> None:
    _make_state_db(env.db_path())
    _db_exec(
        env.db_path(),
        "INSERT OR REPLACE INTO leases VALUES ('run-synthetic-1','w-1','t',?,?,?,60)",
        (time.time(), time.time(), time.time() + 3600),
    )
    before = env.active_bytes()
    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(env.v110.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_UPGRADE_LOCKED", out
    assert "run-synthetic-1" in out["error"]["message"]
    assert env.active_bytes() == before and not env.release_dir("1.1.0").exists() and not env.backups()
    _db_exec(env.db_path(), "DELETE FROM leases")


def test_upgrade_success_preserves_company_and_db_and_switches_active(env: Env) -> None:
    company = env.install_root / "company"
    cfg = company / "config" / "company_config.yaml"
    cfg.write_text("profile: corp-offline\npaths:\n  sources_roots: ['D:/회사 자료']\n", encoding="utf-8")
    tpl = company / "templates" / "review 템플릿.pptx"
    tpl.write_bytes(b"PK\x03\x04 synthetic template bytes")
    ext = company / "extensions" / "corp_ext" / "__init__.py"
    ext.parent.mkdir(parents=True, exist_ok=True)
    ext.write_text("VERSION = '1'\n", encoding="utf-8")
    run_note = env.data_root / "runs" / "run-synthetic-1" / "note.txt"
    run_note.parent.mkdir(parents=True, exist_ok=True)
    run_note.write_text("history\n", encoding="utf-8")
    hashes_before = {p: core.sha256_file(p) for p in (cfg, tpl, ext, run_note, env.db_path())}

    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(env.v110.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == 0, out
    assert (
        out["status"] == "upgraded" and out["release_id"] == "1.1.0" and out["previous_release_id"] == "1.0.0"
    )
    active = env.active()
    assert (
        active["release_id"] == "1.1.0"
        and active["activated_by"] == "upgrade"
        and active["previous"]["release_id"] == "1.0.0"
    )
    assert Path(active["data_root"]) == env.data_root, "data_root 는 active.json 에서 이어받는다"
    assert env.venv_python("1.1.0").is_file() and env.venv_python("1.0.0").is_file(), "구버전 venv 보존"
    log = read_json(env.release_dir("1.1.0") / "install_log.json")
    assert log["status"] == "installed" and {s["name"]: s for s in log["steps"]}["selftest"]["exit_code"] == 0
    # 회사 설정/템플릿/extension/업무 이력/DB 보존
    for p, h in hashes_before.items():
        assert p.is_file() and core.sha256_file(p) == h, p
    assert not (company / "config" / "company_config.example.yaml").exists(), "예시 설정으로 덮어쓰지 않음"
    # backup: sqlite backup API 사본 + company snapshot + migration plan
    bdir = Path(out["backup_dir"])
    assert bdir.parent == env.install_root / "backups" and bdir.name.endswith("-pre-upgrade")
    bm = read_json(bdir / "backup_manifest.json")
    assert (
        bm["db"]["method"] == "sqlite3.Connection.backup"
        and bm["db"]["integrity"] == "ok"
        and bm["db"]["schema_version"] == 4
    )
    assert core.db_schema_version(bdir / "state" / "agent_state.sqlite") == 4
    assert bm["company"] == {"config": 1, "templates": 1, "extensions": 1}
    assert (bdir / "company" / "templates" / "review 템플릿.pptx").read_bytes() == tpl.read_bytes()
    assert set(bm["files"]) >= {"state/agent_state.sqlite", "company/config/company_config.yaml"}
    plan = read_json(bdir / "migration_plan.json")
    assert plan["action"] == "none" and plan["current"] == 4 and plan["target"] == 4
    assert plan["previous_release"] == "1.0.0" and plan["new_release"] == "1.1.0"
    assert read_json(bdir / "upgrade_log.json")["status"] == "upgraded"
    assert out["migration_plan"]["action"] == "none"


def test_upgrade_failure_keeps_old_active(env: Env) -> None:
    before = env.active_bytes()
    backups_before = env.backups()
    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(env.v120_bad.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_INSTALL_FAILED", out
    assert "self-test" in out["error"]["message"]
    assert env.active_bytes() == before and env.active()["release_id"] == "1.1.0"
    failed_log = read_json(env.release_dir("1.2.0") / "install_log.json")
    assert failed_log["status"] == "install_failed" and failed_log["error"]["details"]["step"] == "selftest"
    assert env.backups() == backups_before, "설치 실패 시 백업/전환 단계로 가지 않음"
    # 실패한 폴더에 .venv 가 남아 있으면 재시도 시 --clear 로 덮어쓰지 않고 안내
    rc, out = env.run_json(
        "install.py",
        "--package",
        str(env.v120_bad.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert (
        rc == core.EXIT_RUNTIME and ".venv" in out["error"]["message"] and "--clear" in out["error"]["hint"]
    )


def test_upgrade_already_active_is_noop(env: Env) -> None:
    before = env.active_bytes()
    backups_before = env.backups()
    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(env.v110.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == 0 and out["status"] == "already_active" and out["backup_dir"] is None
    assert env.active_bytes() == before and env.backups() == backups_before


def test_upgrade_incompatible_newer_db_keeps_active(env: Env) -> None:
    """현재 DB schema 가 새 release 의 db_schema_version 보다 높으면 E_ROLLBACK_INCOMPATIBLE, 이전 active 유지."""
    # 1.0.0 (db 4) 이 active 인 상태를 만들고 DB 를 5 로 올린 뒤, db_schema_version=3 인 release 로 upgrade 시도
    low = build_fake_release(env.tmp, version="1.3.0", db_schema_version=3)
    _db_exec(
        env.db_path(), "INSERT OR IGNORE INTO schema_version VALUES (5, '2026-01-02T00:00:00+00:00', 'test')"
    )
    before = env.active_bytes()
    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(low.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_ROLLBACK_INCOMPATIBLE", out
    assert env.active_bytes() == before
    plan = read_json(Path(out["error"]["details"]["migration_plan"]))
    assert plan["action"] == "incompatible_newer" and plan["current"] == 5 and plan["target"] == 3
    _db_exec(env.db_path(), "DELETE FROM schema_version WHERE version = 5")
    shutil.rmtree(env.release_dir("1.3.0"))


# --------------------------------------------------------------------------- rollback
def test_rollback_code_only_keeps_db(env: Env) -> None:
    db_hash = core.sha256_file(env.db_path())
    rc, out = env.run_json("rollback.py", "--to", "1.0.0", "--install-root", str(env.install_root))
    assert rc == 0, out
    assert (
        out["release_id"] == "1.0.0" and out["previous_release_id"] == "1.1.0" and out["restored_db"] is None
    )
    active = env.active()
    assert (
        active["release_id"] == "1.0.0"
        and active["activated_by"] == "rollback"
        and Path(active["venv_python"]) == env.venv_python("1.0.0")
    )
    assert core.sha256_file(env.db_path()) == db_hash
    assert Path(out["log_path"]).is_file() and Path(out["log_path"]).parent == env.install_root / "backups"
    r = env.run("launch.py", "--install-root", str(env.install_root), "--", "version")
    assert r.returncode == 0 and json.loads(r.stdout.strip().splitlines()[-1])["version"] == "1.0.0"


def test_rollback_missing_release_and_lease(env: Env) -> None:
    before = env.active_bytes()
    rc, out = env.run_json("rollback.py", "--to", "9.9.9", "--install-root", str(env.install_root))
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_ROLLBACK_INCOMPATIBLE"
    rc, out = env.run_json("rollback.py", "--to", "1.2.0", "--install-root", str(env.install_root))
    assert (
        rc == core.EXIT_RUNTIME
        and "installed 가 아닙니다" in out["error"]["message"]
        or "venv" in out["error"]["message"]
    )
    _db_exec(
        env.db_path(),
        "INSERT OR REPLACE INTO leases VALUES ('run-synthetic-1','w-2','t',?,?,?,60)",
        (time.time(), time.time(), time.time() + 3600),
    )
    rc, out = env.run_json("rollback.py", "--to", "1.1.0", "--install-root", str(env.install_root))
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_UPGRADE_LOCKED"
    _db_exec(env.db_path(), "DELETE FROM leases")
    assert env.active_bytes() == before


def test_rollback_incompatible_db_requires_restore_db(env: Env) -> None:
    # 1.1.0 으로 다시 올린 뒤(backup 생성) DB schema 가 5 로 올라간 상황을 흉내낸다
    rc, out = env.run_json(
        "upgrade.py",
        "--package",
        str(env.v110.zip_path),
        "--install-root",
        str(env.install_root),
        "--python",
        env.base_python,
    )
    assert rc == 0 and out["status"] == "upgraded" and out["release_id"] == "1.1.0"
    bdir = Path(out["backup_dir"])
    _db_exec(
        env.db_path(), "INSERT OR IGNORE INTO schema_version VALUES (5, '2026-01-02T00:00:00+00:00', 'test')"
    )
    _db_exec(env.db_path(), "INSERT OR IGNORE INTO runs VALUES ('run-after-upgrade', 'train', 'COMPLETED')")
    newer_hash = core.sha256_file(env.db_path())
    before = env.active_bytes()
    # 코드 rollback 만으로는 불가: 안내 + 사용 가능한 backup 목록
    rc, out = env.run_json("rollback.py", "--to", "1.0.0", "--install-root", str(env.install_root))
    assert rc == core.EXIT_RUNTIME and out["error"]["code"] == "E_ROLLBACK_INCOMPATIBLE", out
    assert "--restore-db" in out["error"]["hint"] and "이력" in out["error"]["hint"]
    listed = {b["backup_dir"]: b["schema_version"] for b in out["error"]["details"]["available_backups"]}
    assert listed[str(bdir)] == 4
    assert env.active_bytes() == before and core.sha256_file(env.db_path()) == newer_hash
    # 호환되지 않는(더 높은) backup 지정도 거부 — 현재 DB(5) 를 backup 처럼 지정
    rc, out = env.run_json(
        "rollback.py",
        "--to",
        "1.0.0",
        "--install-root",
        str(env.install_root),
        "--restore-db",
        str(env.db_path()),
    )
    assert rc == core.EXIT_RUNTIME and "schema version 5" in out["error"]["message"]
    # --restore-db: 현재 DB 를 pre-rollback 백업 후 snapshot 복원 + active 전환
    rc, out = env.run_json(
        "rollback.py", "--to", "1.0.0", "--install-root", str(env.install_root), "--restore-db", str(bdir)
    )
    assert rc == 0, out
    assert (
        out["release_id"] == "1.0.0"
        and out["restored_db"]["schema_version"] == 4
        and out["db_schema_version"] == 4
    )
    assert env.active()["release_id"] == "1.0.0"
    assert core.db_schema_version(env.db_path()) == 4
    pre = Path(out["restored_db"]["pre_rollback_backup"])
    assert (
        pre.name.endswith("-pre-rollback")
        and core.db_schema_version(pre / "state" / "agent_state.sqlite") == 5
    )
    assert (pre / "rollback_log.json").is_file() and read_json(pre / "backup_manifest.json")["db"][
        "integrity"
    ] == "ok"
    conn = sqlite3.connect(str(pre / "state" / "agent_state.sqlite"))
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-after-upgrade'").fetchone()[0] == 1
        ), "업데이트 이후 이력은 pre-rollback 백업에 보존"
    finally:
        conn.close()
    conn = sqlite3.connect(str(env.db_path()))
    try:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-after-upgrade'").fetchone()[0] == 0
    finally:
        conn.close()
    # 회사 설정은 rollback 이 건드리지 않음
    assert (
        (env.install_root / "company" / "config" / "company_config.yaml")
        .read_text(encoding="utf-8")
        .startswith("profile: corp-offline")
    )


# --------------------------------------------------------------------------- acceptance
def test_acceptance_json_matches_final_zip(env: Env, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "install-transfer.json").write_text(
        json.dumps(
            {
                "name": "install-transfer",
                "command": "scripts/install.py --package <zip>",
                "exit_code": 0,
                "status": "PASS",
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out 폴더"
    r = env.run(
        "acceptance.py",
        "--zip",
        str(env.v100.zip_path),
        "--evidence-dir",
        str(evidence),
        "--out",
        str(out_dir),
        "--status",
        "TARGET_OFFLINE_TESTED=PASS=호스트와 동일 target 새 경로 설치",
        "--status",
        "HOST_CORE_TESTED=PASS=pytest",
        "--network-note",
        "host network (no isolation)",
        "--json",
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    acc_path = out_dir / (env.v100.zip_path.name[:-4] + ".acceptance.json")
    assert Path(out["output"]) == acc_path and acc_path.is_file()
    doc = read_json(acc_path)
    assert doc["format"] == core.ACCEPTANCE_FORMAT
    assert doc["zip"]["sha256"] == core.sha256_file(env.v100.zip_path) == env.v100.zip_sha256
    assert doc["zip"]["sha256_file_match"] is True and doc["zip"]["name"] == env.v100.zip_path.name
    assert doc["verification"]["TARGET_OFFLINE_TESTED"]["status"] == "PASS"
    assert doc["verification"]["CORP_INSTALLED"]["status"] == "NOT_RUN"
    assert doc["evidence_dir_summary"] == {"count": 1, "pass": 1, "fail": 0}
    assert doc["environment"]["network_isolation"] == "host network (no isolation)"
    assert doc["manifest"]["release_id"] == "1.0.0"
    assert core.sha256_file(env.v100.zip_path) == env.v100.zip_sha256, (
        "acceptance 생성이 ZIP 을 수정하지 않음"
    )
    # 사내 상태 PASS 는 --confirm-corp-environment 없이 거부
    r = env.run(
        "acceptance.py",
        "--zip",
        str(env.v100.zip_path),
        "--out",
        str(out_dir),
        "--status",
        "CORP_INSTALLED=PASS=x",
    )
    assert r.returncode == core.EXIT_USAGE and "confirm-corp-environment" in r.stderr


def test_release_dir_restricts_ids(env: Env) -> None:
    with pytest.raises(core.ScriptError):
        core.release_dir(env.install_root, "../evil", env.profile.profile_id)
    with pytest.raises(core.ScriptError):
        core.release_dir(env.install_root, "1.0.0", "..")
    assert core.release_dir(env.install_root, "1.0.0", env.profile.profile_id) == env.release_dir("1.0.0")
