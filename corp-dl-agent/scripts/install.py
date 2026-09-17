"""install: 반입 패키지 오프라인 설치기 (표준 라이브러리만, 앱 미설치 상태에서 실행).

사용:
  python install.py --package <zip|dir> --install-root <dir> [--data-root <dir>] [--python <path>]
                    [--skip-selftest] [--json] [--target-profile <json>] [--no-activate] [--timeout 3600]

순서 (_common.run_install):
  verify → releases/<release_id>/<profile_id>/ 최종 위치에 추출 (같은 release 가 이미 있고 manifest hash 가 같으면
  재사용, 다르면 중단) → 그 위치에 .venv 생성 (기존 .venv 가 있으면 --clear 로 덮어쓰지 않고 중단; venv/ensurepip
  없으면 exit 8) → pip 환경/설정 차단 → pip install --isolated --no-index --find-links <wheelhouse> --require-hashes
  --only-binary=:all: --no-cache-dir --disable-pip-version-check -r locks/<profile>.txt → pip check → import →
  version smoke → self-test → company/ workspace/ backups/ 폴더 생성(기존 보존) → active.json 원자적 교체.
  실패 시 기존 active.json 유지, 새 폴더에 install_log.json(status=install_failed) 기록.
환경변수 기본값: DIA_INSTALL_ROOT, DIA_DATA_ROOT, PYTHON.
exit code: 0 성공 / 2 사용법 / 5 패키지 검증 실패 / 6 설치 실패(기존 active 유지) / 8 선행 조건(Python/venv/ensurepip) 미충족
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

# 앱 쪽(corp_dl_agent.commands) 과 시험이 같은 이름으로 접근할 수 있도록 core 함수를 재노출한다.
run_install = c.run_install
activate_release = c.activate_release
InstallFailure = c.InstallFailure


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="반입 패키지 오프라인 설치 (표준 라이브러리만). 인터넷/pip index 를 사용하지 않습니다."
    )
    ap.add_argument("--package", required=True, help="반입 ZIP 또는 추출 폴더")
    ap.add_argument(
        "--install-root",
        default=None,
        help="설치 root (기본: DIA_INSTALL_ROOT 또는 %%LOCALAPPDATA%%\\DIA). 한글/공백 가능",
    )
    ap.add_argument(
        "--data-root",
        default=None,
        help="업무 데이터 root (기본: DIA_DATA_ROOT 또는 <install_root>/workspace)",
    )
    ap.add_argument(
        "--python",
        default=None,
        help="venv 를 만들 회사 승인 Python (기본: PYTHON 환경변수 또는 이 스크립트의 base Python)",
    )
    ap.add_argument(
        "--skip-selftest", action="store_true", help="self-test 생략 (install_log 에 skipped 기록)"
    )
    ap.add_argument(
        "--target-profile", default=None, help="검증에 쓸 target profile JSON (기본: 선택한 Python 으로 감지)"
    )
    ap.add_argument(
        "--no-activate", action="store_true", help="설치만 하고 active.json 을 바꾸지 않음 (upgrade 가 사용)"
    )
    ap.add_argument("--timeout", type=int, default=3600, help="각 단계 제한 시간(초)")
    ap.add_argument("--json", action="store_true", help="결과 JSON 출력")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
    data_root = Path(args.data_root).expanduser() if args.data_root else c.default_data_root(install_root)
    python = args.python or c.default_base_python()
    sink = None if args.json else print
    try:
        target = None
        if args.target_profile:
            target = c.read_json(args.target_profile)
            if not isinstance(target, dict):
                raise c.ScriptError("E_PACKAGE_INVALID", "target profile JSON 형식 오류")
        result = c.run_install(
            package=args.package,
            install_root=install_root,
            data_root=data_root,
            python=python,
            skip_selftest=args.skip_selftest,
            activate=not args.no_activate,
            target_profile=target,
            timeout=args.timeout,
            sink=sink,
        )
    except c.ScriptError as exc:
        if args.json:
            print(
                json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2, default=str)
            )
        else:
            print(exc.format_ko(), file=sys.stderr)
            print("기존 active.json 은 변경되지 않았습니다.", file=sys.stderr)
        return exc.exit_code
    if args.json:
        slim = {k: v for k, v in result.items() if k != "manifest"}
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "설치 완료: release {} / {} → {}".format(
                result["release_id"], result["profile_id"], result["release_dir"]
            )
        )
        print("  venv python: {}".format(result["venv_python"]))
        print("  active.json: {}".format("전환됨" if result["activated"] else "보류"))
        print("  설치 로그: {}".format(result["log_path"]))
        print("다음: 04_SelfTest.cmd (또는 scripts/selftest.py) 로 확인 후 03_Start.cmd 로 시작하세요.")
    return c.EXIT_OK


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
