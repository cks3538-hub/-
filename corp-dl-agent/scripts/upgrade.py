"""upgrade: 새 release ZIP 을 설치하고 (active 미전환) DB backup + 회사 설정/템플릿 snapshot + migration plan 을 만든 뒤
모두 성공하면 active.json 을 전환한다. 실패하면 이전 active 를 유지한다.

사용: python upgrade.py --package <zip|dir> [--install-root DIR] [--data-root DIR] [--python PATH] [--skip-selftest] [--json]
순서:
  1. 실행 중 작업 검사: 상태 DB(<data_root>/state/agent_state.sqlite) 의 leases 테이블에서 만료되지 않은 lease 가 있으면
     E_UPGRADE_LOCKED (exit 6). (leases 테이블/DB 가 없으면 잠금 없음)
  2. install.py 로직으로 새 release 설치 (releases/<id>/<profile>/, active 미전환; 동일 release 면 재사용)
  3. backups/<시각>/ 에 DB 를 sqlite3 backup API 로 복사 + company/config, company/templates 복사 + backup_manifest.json
  4. migration_plan.json: 현재 DB schema version vs 새 release 의 db_schema_version. 같으면 no-op, 낮으면 사본에 migration 적용·검증
     후 교체(앱 module 의 StateDB 를 새 venv 로 실행), 높으면 E_ROLLBACK_INCOMPATIBLE 로 중단.
  5. active.json 원자적 전환 + upgrade_log.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
import install  # noqa: E402

MIGRATE_SNIPPET = (
    "import json,sys\n"
    "from {mod}.state.db import StateDB\n"
    "db=StateDB(sys.argv[1])\n"
    "plan=db.migrate()\n"
    "plan['integrity']=db.integrity_check()\n"
    "db.checkpoint(); db.close()\n"
    "print(json.dumps(plan, default=str))\n"
)


def _copy_tree_preserve(src: Path, dst: Path) -> int:
    """src 를 dst 로 복사 (symlink 미추적). 파일 수 반환. src 없으면 0."""
    if not src.is_dir():
        return 0
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = [dn for dn in dirnames if not (d / dn).is_symlink()]
        for fn in filenames:
            fp = d / fn
            if fp.is_symlink():
                continue
            out = dst / fp.relative_to(src)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, out)
            n += 1
    return n


def make_backup(install_root: Path, data_root: Path, *, label: str = "") -> dict[str, Any]:
    """backups/<시각>[-<label>]/ 에 DB(backup API) + company/config + company/templates 를 넣는다."""
    ts = c.timestamp_slug()
    bdir = install_root / "backups" / (f"{ts}-{label}" if label else ts)
    i = 1
    while bdir.exists():
        i += 1
        bdir = install_root / "backups" / (f"{ts}-{label}-{i}" if label else f"{ts}-{i}")
    bdir.mkdir(parents=True)
    info: dict[str, Any] = {"backup_dir": str(bdir), "created_at": c.now_iso(), "db": None, "company": {}}
    db_path = data_root / c.STATE_DB_RELATIVE
    if db_path.is_file():
        info["db"] = c.sqlite_backup(db_path, bdir / "state" / db_path.name)
    else:
        info["db"] = {"path": None, "note": "상태 DB 없음 (아직 run 이력 없음)"}
    for sub in ("config", "templates", "extensions"):
        n = _copy_tree_preserve(install_root / "company" / sub, bdir / "company" / sub)
        info["company"][sub] = n
    files: dict[str, str] = {}
    for fp in sorted(p for p in bdir.rglob("*") if p.is_file()):
        files[fp.relative_to(bdir).as_posix()] = c.sha256_file(fp)
    info["files"] = files
    c.write_json_atomic(bdir / "backup_manifest.json", info)
    return info


def migration_plan(db_path: Path, target_version: int) -> dict[str, Any]:
    current = c.db_schema_version(db_path)
    if current is None:
        action = "none"
        note = f"상태 DB 없음: 새 버전이 첫 실행 때 schema {target_version} 로 생성"
    elif current == target_version:
        action = "none"
        note = "schema version 동일 (no-op)"
    elif current < target_version:
        action = "migrate"
        note = "사본에 migration 적용·검증 후 교체 (실패 시 이전 DB/active 유지)"
    else:
        action = "incompatible_newer"
        note = f"현재 DB schema {current} 가 새 release 의 {target_version} 보다 새롭습니다. 자동 downgrade 하지 않습니다."
    return {
        "db_path": str(db_path),
        "current": current,
        "target": target_version,
        "action": action,
        "note": note,
        "created_at": c.now_iso(),
    }


def apply_migration_on_copy(
    plan: dict[str, Any], backup_db: Path, live_db: Path, venv_py: Path, app_module: str, env: dict[str, str]
) -> dict[str, Any]:
    """backup 사본을 복사해 새 venv 로 migration 을 적용·검증한 뒤 live DB 를 교체한다."""
    work = backup_db.with_name(backup_db.stem + ".migrated" + backup_db.suffix)
    shutil.copy2(backup_db, work)
    r = c.run_logged(
        [str(venv_py), "-c", MIGRATE_SNIPPET.format(mod=app_module), str(work)], env=env, timeout=1800
    )
    if r["exit_code"] != 0:
        raise c.ScriptError(
            "E_INSTALL_FAILED", "DB migration (사본) 실패", details={"stderr_tail": r["stderr_tail"][-1500:]}
        )
    try:
        applied = json.loads(r["stdout_tail"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        applied = {}
    if str(applied.get("integrity", "ok")) != "ok":
        raise c.ScriptError(
            "E_INSTALL_FAILED", "migration 사본 integrity_check 실패", details={"result": applied}
        )
    for suffix in ("-wal", "-shm"):
        side = live_db.with_name(live_db.name + suffix)
        if side.exists() and side.stat().st_size > 0 and suffix == "-wal":
            raise c.ScriptError(
                "E_UPGRADE_LOCKED",
                "상태 DB 의 WAL 이 비어 있지 않습니다 (다른 프로세스가 열고 있을 수 있음)",
                hint="앱을 모두 종료한 뒤 다시 시도하세요.",
            )
    for suffix in ("-wal", "-shm"):
        side = live_db.with_name(live_db.name + suffix)
        if side.exists():
            side.unlink()
    os.replace(work, live_db)
    return {"applied": applied, "replaced": str(live_db)}


def run_upgrade(
    *,
    package: str,
    install_root: Path,
    data_root: Path,
    python: str,
    skip_selftest: bool,
    sink: Any = None,
) -> dict[str, Any]:
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "started_at": c.now_iso(),
        "package": str(package),
        "install_root": str(install_root),
        "data_root": str(data_root),
        "steps": [],
    }
    previous = c.read_active(install_root)
    log["previous_active"] = previous
    db_path = data_root / c.STATE_DB_RELATIVE

    # 1. 실행 중 작업 잠금
    leases = c.active_leases(db_path)
    running = c.running_runs(db_path)
    log["steps"].append({"name": "lock_check", "active_leases": leases, "running_runs": running})
    if leases:
        raise c.ScriptError(
            "E_UPGRADE_LOCKED",
            "실행 중인 작업(lease) 이 있어 업데이트를 시작할 수 없습니다: {}".format(
                ", ".join(str(x["run_id"]) for x in leases)
            ),
            hint="작업을 완료하거나 pause 한 뒤(lease 만료 후) 다시 시도하세요. 실행 중 worker 가 쓰는 파일은 교체하지 않습니다.",
            details={"leases": leases},
        )
    if running:
        log["warnings"] = [f"lease 없이 RUNNING 상태인 run 이 있습니다 (비정상 종료 가능): {running}"]
        install._emit(sink, f"경고: lease 없이 RUNNING 상태인 run: {running}")

    # 2. 새 release 설치 (active 미전환)
    install._emit(sink, "새 release 설치 (active 미전환)")
    res = install.run_install(
        package=package,
        install_root=install_root,
        data_root=data_root,
        python=python,
        skip_selftest=skip_selftest,
        activate=False,
        sink=sink,
    )
    manifest = res["manifest"]
    log["new_release"] = {
        k: res[k] for k in ("release_id", "profile_id", "release_dir", "venv_python", "reused")
    }
    if (
        previous
        and previous.get("release_id") == res["release_id"]
        and previous.get("profile_id") == res["profile_id"]
    ):
        install._emit(sink, "이미 활성인 release 와 동일합니다 (manifest 일치). 백업/전환은 생략합니다.")
        log["status"] = "already_active"
        log["finished_at"] = c.now_iso()
        return {"ok": True, "status": "already_active", "release_id": res["release_id"], "log": log}

    # 3. 백업
    install._emit(sink, "DB backup (sqlite backup API) + company/config·templates snapshot")
    backup = make_backup(install_root, data_root, label="pre-upgrade")
    log["backup"] = backup
    bdir = Path(backup["backup_dir"])

    # 4. migration plan
    plan = migration_plan(db_path, int(manifest["db_schema_version"]))
    plan["previous_release"] = previous.get("release_id") if previous else None
    plan["new_release"] = res["release_id"]
    c.write_json_atomic(bdir / "migration_plan.json", plan)
    log["migration_plan"] = plan
    if plan["action"] == "incompatible_newer":
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            plan["note"],
            hint="새 release 의 db_schema_version 이 현재 DB 보다 낮습니다. 이전 active 를 유지합니다.",
            details={"migration_plan": str(bdir / "migration_plan.json")},
        )
    if plan["action"] == "migrate":
        if manifest["app_module"] != "corp_dl_agent":
            raise c.ScriptError(
                "E_NOT_SUPPORTED",
                "앱 module {} 의 DB migration 방법을 알지 못합니다".format(manifest["app_module"]),
            )
        backup_db = bdir / "state" / db_path.name
        install._emit(sink, "DB migration 을 사본에 적용·검증 후 교체")
        plan["result"] = apply_migration_on_copy(
            plan, backup_db, db_path, Path(res["venv_python"]), manifest["app_module"], c.sanitized_env()
        )
        c.write_json_atomic(bdir / "migration_plan.json", plan)

    # 5. active 전환
    install._emit(sink, "active.json 전환")
    active = install.activate_release(
        install_root,
        data_root,
        manifest,
        Path(res["release_dir"]),
        Path(res["venv_python"]),
        reason="upgrade",
    )
    log["active"] = active
    log["status"] = "upgraded"
    log["finished_at"] = c.now_iso()
    c.write_json_atomic(bdir / "upgrade_log.json", log)
    return {
        "ok": True,
        "status": "upgraded",
        "release_id": res["release_id"],
        "profile_id": res["profile_id"],
        "previous_release_id": previous.get("release_id") if previous else None,
        "backup_dir": str(bdir),
        "migration_plan": plan,
        "log": log,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="새 release 설치 + DB backup/migration plan + active 전환 (표준 라이브러리만)"
    )
    ap.add_argument("--package", required=True, help="새 반입 ZIP 또는 추출 폴더")
    ap.add_argument("--install-root", default=None, help="설치 root (기본: DIA_INSTALL_ROOT)")
    ap.add_argument(
        "--data-root", default=None, help="데이터 root (기본: DIA_DATA_ROOT 또는 active.json 의 data_root)"
    )
    ap.add_argument(
        "--python",
        default=None,
        help="venv 를 만들 회사 승인 Python (기본: PYTHON 환경변수 또는 base Python)",
    )
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    active = c.read_active(install_root)
    if args.data_root:
        data_root = Path(args.data_root).expanduser()
    elif os.environ.get("DIA_DATA_ROOT", "").strip():
        data_root = Path(os.environ["DIA_DATA_ROOT"]).expanduser()
    elif active and active.get("data_root"):
        data_root = Path(str(active["data_root"]))
    else:
        data_root = c.default_data_root(install_root)
    python = args.python or c.default_base_python()
    sink = None if args.json else print
    try:
        result = run_upgrade(
            package=args.package,
            install_root=install_root,
            data_root=data_root,
            python=python,
            skip_selftest=args.skip_selftest,
            sink=sink,
        )
    except c.ScriptError as exc:
        if args.json:
            print(
                json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2, default=str)
            )
        else:
            print(exc.format_ko(), file=sys.stderr)
            print("이전 active.json 은 유지됩니다.", file=sys.stderr)
        return exc.exit_code
    if args.json:
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "log"}, ensure_ascii=False, indent=2, default=str
            )
        )
    else:
        print(
            "업데이트 {}: release {} (이전 {})".format(
                result["status"], result["release_id"], result.get("previous_release_id")
            )
        )
        if result.get("backup_dir"):
            print("  백업: {}".format(result["backup_dir"]))
            print(
                "  migration: {} ({})".format(
                    result["migration_plan"]["action"], result["migration_plan"]["note"]
                )
            )
        print(
            "다음: 04_SelfTest.cmd 로 확인하세요. 문제가 있으면 06_Rollback.cmd (rollback --to <이전 버전>)."
        )
    return c.EXIT_OK


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
