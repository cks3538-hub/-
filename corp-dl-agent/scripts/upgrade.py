"""upgrade: 새 release ZIP 을 설치하고 (active 미전환) DB backup + 회사 설정/템플릿 snapshot + migration plan 을 만든 뒤
모두 성공하면 active.json 을 전환한다. 실패하면 이전 active 를 유지한다. (로직: _common.run_upgrade)

사용: python upgrade.py --package <zip|dir> [--install-root DIR] [--data-root DIR] [--python PATH] [--skip-selftest] [--json]
순서:
  1. 실행 중 작업 검사: 상태 DB(<data_root>/state/agent_state.sqlite) 의 leases 테이블에서 만료되지 않은 lease 가 있으면
     E_UPGRADE_LOCKED (exit 6). (leases 테이블/DB 가 없으면 잠금 없음)
  2. install 로직으로 새 release 설치 (releases/<id>/<profile>/, active 미전환; 동일 release 면 재사용)
  3. backups/<시각>-pre-upgrade/ 에 DB 를 sqlite3 backup API 로 복사 + company/config·templates·extensions 복사 + backup_manifest.json
  4. migration_plan.json: 현재 DB schema version vs 새 release 의 db_schema_version. 같으면 no-op, 낮으면 사본에 migration 적용·검증
     후 교체(앱 module 의 StateDB 를 새 venv 로 실행), 높으면 E_ROLLBACK_INCOMPATIBLE 로 중단.
  5. active.json 원자적 전환 + upgrade_log.json
exit code: 0 / 5 패키지 검증 실패 / 6 잠금·설치·호환 실패(이전 active 유지) / 8 선행 조건 미충족 / 9 migration 미지원 앱
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

run_upgrade = c.run_upgrade
make_backup = c.make_backup
migration_plan = c.migration_plan


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
    ap.add_argument("--skip-selftest", action="store_true", help="설치 후 self-test 생략 (권장하지 않음)")
    ap.add_argument("--json", action="store_true", help="결과 JSON 출력")
    args = ap.parse_args(argv)
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    data_root = c.resolve_data_root(install_root, args.data_root, c.read_active(install_root))
    python = args.python or c.default_base_python()
    sink = None if args.json else print
    try:
        result = c.run_upgrade(
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
