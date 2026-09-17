"""upgrade / rollback 명령: corp_dl_agent.packaging.verifier_core (= scripts/_common.py) 의 설치/백업/전환 로직을
in-process 로 실행한다 (별도 스크립트 프로세스 없음).

- upgrade  --package <zip|dir> [--install-root DIR] [--data-root DIR] [--python PATH] [--skip-selftest] [--json]
- rollback --to <release_id> [--target-profile-id <profile_id>] [--install-root DIR] [--data-root DIR]
           [--restore-db <backups/<시각>>] [--json]

설치 root 결정 순서: --install-root > 설정 paths.install_root > 환경변수 DIA_INSTALL_ROOT > 기본값(%LOCALAPPDATA%\\DIA 등).
data root 결정 순서: --data-root > DIA_DATA_ROOT > active.json 의 data_root > 설정 paths.data_root(설정 파일 지정 시) > <install_root>/workspace.
venv 용 Python: --python > PYTHON 환경변수 > 현재 release venv 의 base Python > sys.executable.
exit code: 0 / 5 패키지 검증 실패 / 6 잠금·설치·비호환(이전 active 유지) / 8 선행 조건 미충족 / 9 migration 미지원.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.config import load_config
from corp_dl_agent.errors import AgentError
from corp_dl_agent.packaging import verifier_core as core


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    u = sub.add_parser(
        "upgrade",
        help="새 release ZIP 설치 (실행 중 작업 잠금 검사, DB backup/migration plan, 성공 시 active 전환)",
    )
    add_common_arguments(u)
    u.add_argument("--package", required=True, help="새 반입 ZIP 또는 추출 폴더")
    u.add_argument(
        "--install-root", default=None, help="설치 root (기본: 설정 paths.install_root / DIA_INSTALL_ROOT)"
    )
    u.add_argument("--data-root", default=None, help="업무 데이터 root (기본: DIA_DATA_ROOT / active.json)")
    u.add_argument(
        "--python",
        default=None,
        help="venv 를 만들 회사 승인 Python (기본: PYTHON 환경변수 또는 현재 release 의 base Python)",
    )
    u.add_argument("--skip-selftest", action="store_true", help="설치 후 self-test 생략 (권장하지 않음)")
    u.set_defaults(handler=run_upgrade)

    r = sub.add_parser(
        "rollback", help="이전 release 로 active 전환 (코드 rollback; DB rollback 은 --restore-db 로 분리)"
    )
    add_common_arguments(r)
    r.add_argument("--to", required=True, help="되돌릴 release_id (예: 4.0.0)")
    r.add_argument(
        "--target-profile-id", default=None, help="release 의 profile_id (기본: 현재 active 와 동일)"
    )
    r.add_argument(
        "--install-root", default=None, help="설치 root (기본: 설정 paths.install_root / DIA_INSTALL_ROOT)"
    )
    r.add_argument("--data-root", default=None, help="업무 데이터 root (기본: DIA_DATA_ROOT / active.json)")
    r.add_argument(
        "--restore-db",
        default=None,
        help="복원할 DB backup 폴더 (backups/<시각>) 또는 sqlite 파일. 현재 DB 는 pre-rollback 백업 후 교체",
    )
    r.set_defaults(handler=run_rollback)


def _wrap(exc: core.ScriptError) -> AgentError:
    return AgentError(exc.code, exc.message, hint=exc.hint or None, details=exc.details)


def resolve_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """(install_root, data_root). 설정 파일은 명시된 경우에만 읽는다 (기본 설정에는 install_root 가 없음)."""
    cfg = None
    if (
        getattr(args, "config_path", None)
        or getattr(args, "overrides", None)
        or getattr(args, "profile", None)
    ):
        cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    if getattr(args, "install_root", None):
        install_root = Path(args.install_root).expanduser()
    elif cfg is not None and cfg.paths.install_root:
        install_root = Path(cfg.paths.install_root).expanduser()
    else:
        install_root = core.default_install_root()
    active = core.read_active(install_root)
    explicit_data = getattr(args, "data_root", None)
    if (
        not explicit_data
        and cfg is not None
        and args.config_path
        and not (active and active.get("data_root"))
    ):
        explicit_data = cfg.paths.data_root
    data_root = core.resolve_data_root(install_root, explicit_data, active)
    return install_root, data_root


def _print_error(args: argparse.Namespace, exc: AgentError, keep_note: str) -> None:
    import sys

    if args.json_output:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2, default=str))
    else:
        print(exc.format_ko(), file=sys.stderr)
        print(keep_note, file=sys.stderr)


def run_upgrade(args: argparse.Namespace) -> int:
    install_root, data_root = resolve_roots(args)
    python = args.python or core.default_base_python()
    sink: Any = None if args.json_output else print
    try:
        result = core.run_upgrade(
            package=args.package,
            install_root=install_root,
            data_root=data_root,
            python=python,
            skip_selftest=bool(args.skip_selftest),
            sink=sink,
        )
    except core.ScriptError as exc:
        err = _wrap(exc)
        _print_error(args, err, "이전 active.json 은 유지됩니다.")
        return err.exit_code
    if args.json_output:
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
            plan = result.get("migration_plan") or {}
            print("  migration: {} ({})".format(plan.get("action"), plan.get("note")))
        print("다음: self-test 로 확인하세요. 문제가 있으면 rollback --to <이전 버전>.")
    return 0


def run_rollback(args: argparse.Namespace) -> int:
    install_root, data_root = resolve_roots(args)
    sink: Any = None if args.json_output else print
    try:
        result = core.run_rollback(
            to=args.to,
            profile_id=args.target_profile_id,
            install_root=install_root,
            data_root=data_root,
            restore_db=args.restore_db,
            sink=sink,
        )
    except core.ScriptError as exc:
        err = _wrap(exc)
        _print_error(args, err, "active.json 은 변경되지 않았습니다.")
        return err.exit_code
    if args.json_output:
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
    return 0
