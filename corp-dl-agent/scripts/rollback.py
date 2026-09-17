"""rollback: 이전 release 로 active.json 을 되돌린다. 코드 rollback 과 DB rollback 을 분리한다.

사용: python rollback.py --to <release_id> [--profile <profile_id>] [--install-root DIR] [--data-root DIR]
                         [--restore-db <backups/<시각> 폴더 또는 DB 파일>] [--json]
- 대상 releases/<to>/<profile>/ 폴더·manifest·venv 존재를 확인한다 (재설치 없음).
- 현재 DB schema version 이 대상 release 의 db_schema_version 보다 높으면 --restore-db 없이는 E_ROLLBACK_INCOMPATIBLE (exit 6):
  active 포인터만 되돌리면 구버전이 새 DB 를 읽을 수 없다. 사용 가능한 backup 목록과 영향(업데이트 이후 이력 손실)을 안내한다.
- --restore-db: 현재 DB 를 backups/pre-rollback-<시각>/ 에 먼저 백업(sqlite backup API)한 뒤 지정 backup 을 복원한다.
  복원 DB 의 schema version 이 대상 release 와 호환되어야 한다. 회사 설정/템플릿은 건드리지 않는다.
- 실행 중 lease 가 있으면 전환하지 않는다.
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
import upgrade  # noqa: E402


def list_db_backups(install_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    bdir = install_root / "backups"
    if not bdir.is_dir():
        return out
    for d in sorted(bdir.iterdir()):
        db = d / "state" / "agent_state.sqlite"
        if d.is_dir() and db.is_file():
            out.append(
                {
                    "backup_dir": str(d),
                    "db": str(db),
                    "schema_version": c.db_schema_version(db),
                    "size_bytes": db.stat().st_size,
                }
            )
    return out


def resolve_restore_db(spec: str) -> Path:
    p = Path(spec).expanduser()
    if p.is_dir():
        cand = p / "state" / "agent_state.sqlite"
        if cand.is_file():
            return cand
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE", f"backup 폴더에 state/agent_state.sqlite 가 없습니다: {p}"
        )
    if p.is_file():
        return p
    raise c.ScriptError("E_ROLLBACK_INCOMPATIBLE", f"복원할 DB backup 이 없습니다: {p}")


def run_rollback(
    *,
    to: str,
    profile_id: Optional[str],
    install_root: Path,
    data_root: Path,
    restore_db: Optional[str],
    sink: Any = None,
) -> dict[str, Any]:
    install_root = Path(install_root).expanduser()
    data_root = Path(data_root).expanduser()
    log: dict[str, Any] = {
        "started_at": c.now_iso(),
        "to": to,
        "install_root": str(install_root),
        "data_root": str(data_root),
    }
    current = c.read_active(install_root)
    log["previous_active"] = current
    if profile_id is None:
        if current and current.get("profile_id"):
            profile_id = str(current["profile_id"])
        else:
            rel = install_root / "releases" / to
            subs = [d.name for d in rel.iterdir() if d.is_dir()] if rel.is_dir() else []
            if len(subs) != 1:
                raise c.ScriptError(
                    "E_ROLLBACK_INCOMPATIBLE",
                    f"profile_id 를 결정할 수 없습니다 (--profile 지정 필요): {subs}",
                )
            profile_id = subs[0]
    dest = c.release_dir(install_root, to, profile_id)
    manifest_path = dest / c.MANIFEST_NAME
    if not manifest_path.is_file():
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            f"대상 release 가 설치되어 있지 않습니다: {dest}",
            hint="releases/ 아래에 남아 있는 버전만 rollback 할 수 있습니다 (재설치는 install.py).",
        )
    manifest = c.read_json(manifest_path)
    problems = c.manifest_problems(manifest)
    if problems:
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            "대상 release 의 manifest 가 올바르지 않습니다",
            details={"problems": problems[:10]},
        )
    venv_py = c.venv_python_path(dest / ".venv")
    if not venv_py.is_file():
        raise c.ScriptError("E_ROLLBACK_INCOMPATIBLE", f"대상 release 의 venv python 이 없습니다: {venv_py}")
    inst_log: dict[str, Any] = {}
    if (dest / c.INSTALL_LOG_NAME).is_file():
        try:
            inst_log = c.read_json(dest / c.INSTALL_LOG_NAME)
        except (OSError, ValueError):
            inst_log = {}
    if inst_log.get("status") not in ("installed", None):
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            "대상 release 의 설치 상태가 installed 가 아닙니다: {}".format(inst_log.get("status")),
        )

    db_path = data_root / c.STATE_DB_RELATIVE
    leases = c.active_leases(db_path)
    if leases:
        raise c.ScriptError(
            "E_UPGRADE_LOCKED",
            "실행 중인 작업(lease) 이 있어 전환하지 않습니다: {}".format([x["run_id"] for x in leases]),
            hint="작업 완료/pause 후 다시 시도하세요.",
        )

    target_db_version = int(manifest["db_schema_version"])
    current_db_version = c.db_schema_version(db_path)
    log["db"] = {
        "path": str(db_path),
        "current_schema_version": current_db_version,
        "target_release_db_schema_version": target_db_version,
    }
    restored: Optional[dict[str, Any]] = None
    if restore_db:
        src_db = resolve_restore_db(restore_db)
        src_version = c.db_schema_version(src_db)
        if src_version is not None and src_version > target_db_version:
            raise c.ScriptError(
                "E_ROLLBACK_INCOMPATIBLE",
                f"복원할 backup 의 schema version {src_version} 이 대상 release 의 {target_db_version} 보다 높습니다",
            )
        install._emit(sink, f"현재 DB 를 pre-rollback 백업 후 {src_db} 을 복원")
        pre = upgrade.make_backup(install_root, data_root, label="pre-rollback")
        log["pre_rollback_backup"] = pre
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists() and suffix == "-wal" and side.stat().st_size > 0:
                raise c.ScriptError(
                    "E_UPGRADE_LOCKED",
                    "상태 DB 의 WAL 이 비어 있지 않습니다 (앱이 열고 있을 수 있음)",
                    hint="앱을 모두 종료한 뒤 다시 시도하세요.",
                )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.with_name(f".{db_path.name}.restore-{os.getpid()}")
        shutil.copy2(src_db, tmp)
        if c.db_schema_version(tmp) != src_version:
            tmp.unlink()
            raise c.ScriptError("E_ROLLBACK_INCOMPATIBLE", "복원 사본 검증 실패")
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists():
                side.unlink()
        os.replace(tmp, db_path)
        restored = {
            "from": str(src_db),
            "schema_version": src_version,
            "sha256": c.sha256_file(db_path),
            "pre_rollback_backup": pre["backup_dir"],
        }
        log["restored_db"] = restored
        current_db_version = src_version
    if current_db_version is not None and current_db_version > target_db_version:
        backups = list_db_backups(install_root)
        raise c.ScriptError(
            "E_ROLLBACK_INCOMPATIBLE",
            f"현재 DB schema {current_db_version} 가 대상 release {to} 의 schema {target_db_version} 보다 새롭습니다. active 포인터만 되돌리면 구버전이 DB 를 읽을 수 없습니다.",
            hint="--restore-db <backups/<시각>> 로 호환되는 DB snapshot 을 복원하면서 rollback 할 수 있습니다. 업데이트 이후 추가된 run/문서 이력은 사라집니다 (현재 DB 는 pre-rollback 백업으로 보존).",
            details={
                "available_backups": [
                    {"backup_dir": b["backup_dir"], "schema_version": b["schema_version"]} for b in backups
                ]
            },
        )
    active = install.activate_release(install_root, data_root, manifest, dest, venv_py, reason="rollback")
    log["active"] = active
    log["finished_at"] = c.now_iso()
    rdir = Path(restored["pre_rollback_backup"]) if restored else install_root / "backups"
    rdir.mkdir(parents=True, exist_ok=True)
    log_path = rdir / ("rollback_log.json" if restored else f"rollback-{c.timestamp_slug()}.json")
    c.write_json_atomic(log_path, log)
    return {
        "ok": True,
        "release_id": to,
        "profile_id": profile_id,
        "previous_release_id": current.get("release_id") if current else None,
        "restored_db": restored,
        "db_schema_version": current_db_version,
        "log_path": str(log_path),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="이전 release 로 active 전환 (코드 rollback; DB rollback 은 --restore-db)"
    )
    ap.add_argument("--to", required=True, help="되돌릴 release_id")
    ap.add_argument("--profile", default=None, help="profile_id (기본: 현재 active 와 동일)")
    ap.add_argument("--install-root", default=None)
    ap.add_argument("--data-root", default=None)
    ap.add_argument(
        "--restore-db", default=None, help="복원할 DB backup 폴더(backups/<시각>) 또는 sqlite 파일"
    )
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
    sink = None if args.json else print
    try:
        result = run_rollback(
            to=args.to,
            profile_id=args.profile,
            install_root=install_root,
            data_root=data_root,
            restore_db=args.restore_db,
            sink=sink,
        )
    except c.ScriptError as exc:
        if args.json:
            print(
                json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2, default=str)
            )
        else:
            print(exc.format_ko(), file=sys.stderr)
            print("active.json 은 변경되지 않았습니다.", file=sys.stderr)
        return exc.exit_code
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "rollback 완료: active → {} / {} (이전 {})".format(
                result["release_id"], result["profile_id"], result["previous_release_id"]
            )
        )
        if result["restored_db"]:
            print(
                "  DB 복원: {} (현재 DB 는 {} 에 백업)".format(
                    result["restored_db"]["from"], result["restored_db"]["pre_rollback_backup"]
                )
            )
        else:
            print("  DB 는 변경하지 않았습니다 (코드 rollback 만).")
        print("  로그: {}".format(result["log_path"]))
    return c.EXIT_OK


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
