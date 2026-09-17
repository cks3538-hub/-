"""rollback: 이전 release 로 active.json 을 되돌린다. 코드 rollback 과 DB rollback 을 분리한다. (로직: _common.run_rollback)

사용: python rollback.py --to <release_id> [--profile <profile_id>] [--install-root DIR] [--data-root DIR]
                         [--restore-db <backups/<시각> 폴더 또는 DB 파일>] [--json]
- 대상 releases/<to>/<profile>/ 폴더·manifest·venv 존재를 확인한다 (재설치 없음).
- 현재 DB schema version 이 대상 release 의 db_schema_version 보다 높으면 --restore-db 없이는 E_ROLLBACK_INCOMPATIBLE (exit 6):
  active 포인터만 되돌리면 구버전이 새 DB 를 읽을 수 없다. 사용 가능한 backup 목록과 영향(업데이트 이후 이력 손실)을 안내한다.
- --restore-db: 현재 DB 를 backups/<시각>-pre-rollback/ 에 먼저 백업(sqlite backup API)한 뒤 지정 backup 을 복원한다.
  복원 DB 의 schema version 이 대상 release 와 호환되어야 한다. 회사 설정/템플릿은 건드리지 않는다.
- 실행 중 lease 가 있으면 전환하지 않는다.
exit code: 0 / 6 비호환·미설치·잠금 (active.json 변경 없음)
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

run_rollback = c.run_rollback
list_db_backups = c.list_db_backups


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="이전 release 로 active 전환 (코드 rollback; DB rollback 은 --restore-db)"
    )
    ap.add_argument("--to", required=True, help="되돌릴 release_id")
    ap.add_argument("--profile", default=None, help="profile_id (기본: 현재 active 와 동일)")
    ap.add_argument("--install-root", default=None, help="설치 root (기본: DIA_INSTALL_ROOT)")
    ap.add_argument(
        "--data-root", default=None, help="데이터 root (기본: DIA_DATA_ROOT 또는 active.json 의 data_root)"
    )
    ap.add_argument(
        "--restore-db", default=None, help="복원할 DB backup 폴더(backups/<시각>) 또는 sqlite 파일"
    )
    ap.add_argument("--json", action="store_true", help="결과 JSON 출력")
    args = ap.parse_args(argv)
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    data_root = c.resolve_data_root(install_root, args.data_root, c.read_active(install_root))
    sink = None if args.json else print
    try:
        result = c.run_rollback(
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
