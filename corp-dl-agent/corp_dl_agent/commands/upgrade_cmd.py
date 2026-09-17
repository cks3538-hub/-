"""upgrade / rollback 명령: 설치된 release 의 표준 라이브러리 스크립트(scripts/upgrade.py, scripts/rollback.py) 를 실행한다.

- upgrade  --package <zip> [--install-root DIR] [--data-root DIR] [--python PATH] [--skip-selftest] [--json]
- rollback --to <release_id> [--profile <profile_id>] [--install-root DIR] [--data-root DIR] [--restore-db <backup dir>] [--json]

설치 root 결정 순서: --install-root > 설정 paths.install_root > 환경변수 DIA_INSTALL_ROOT > 기본값(%LOCALAPPDATA%\\DIA 등).
스크립트 위치: active.json 의 release_dir/scripts (설치본) > 프로젝트 소스의 scripts/ (개발 환경).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

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
    u.add_argument("--package", required=True, help="새 반입 ZIP 경로")
    u.add_argument("--install-root", default=None, help="설치 root (기본: 설정/DIA_INSTALL_ROOT)")
    u.add_argument("--data-root", default=None, help="업무 데이터 root (기본: 설정/DIA_DATA_ROOT)")
    u.add_argument(
        "--python", default=None, help="venv 를 만들 회사 승인 Python (기본: 현재 release 의 base Python)"
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
    r.add_argument("--install-root", default=None)
    r.add_argument("--data-root", default=None)
    r.add_argument(
        "--restore-db",
        default=None,
        help="복원할 DB backup 폴더 (backups/<시각>). 현재 DB 는 pre-rollback 백업 후 교체",
    )
    r.set_defaults(handler=run_rollback)


def resolve_install_root(args: argparse.Namespace) -> Path:
    if getattr(args, "install_root", None):
        return Path(args.install_root).expanduser()
    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    if cfg.paths.install_root:
        return Path(cfg.paths.install_root).expanduser()
    return core.default_install_root()


def find_scripts_dir(install_root: Path) -> Path:
    active = core.read_active(install_root)
    if active and active.get("release_dir"):
        cand = Path(str(active["release_dir"])) / "scripts"
        if (cand / "upgrade.py").is_file() and (cand / "rollback.py").is_file():
            return cand
    dev = Path(__file__).resolve().parents[2] / "scripts"
    if (dev / "upgrade.py").is_file():
        return dev
    raise AgentError(
        "E_NOT_SUPPORTED",
        "upgrade/rollback 스크립트를 찾을 수 없습니다 (설치된 release 의 scripts/ 또는 프로젝트 scripts/)",
        hint="반입 ZIP 을 풀어 scripts/05_Update.cmd 또는 scripts/upgrade.py 를 직접 실행하세요.",
        details={"install_root": str(install_root)},
    )


def _run_script(script: Path, argv: list[str]) -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run([sys.executable, str(script), *argv], env=env)
    return int(proc.returncode)


def run_upgrade(args: argparse.Namespace) -> int:
    install_root = resolve_install_root(args)
    scripts = find_scripts_dir(install_root)
    argv = ["--package", args.package, "--install-root", str(install_root)]
    if args.data_root:
        argv += ["--data-root", args.data_root]
    if args.python:
        argv += ["--python", args.python]
    if args.skip_selftest:
        argv.append("--skip-selftest")
    if args.json_output:
        argv.append("--json")
    return _run_script(scripts / "upgrade.py", argv)


def run_rollback(args: argparse.Namespace) -> int:
    install_root = resolve_install_root(args)
    scripts = find_scripts_dir(install_root)
    argv = ["--to", args.to, "--install-root", str(install_root)]
    if args.target_profile_id:
        argv += ["--profile", args.target_profile_id]
    if args.data_root:
        argv += ["--data-root", args.data_root]
    if args.restore_db:
        argv += ["--restore-db", args.restore_db]
    if args.json_output:
        argv.append("--json")
    return _run_script(scripts / "rollback.py", argv)
