"""verify: 반입 패키지 검증 (표준 라이브러리만, 앱 미설치 상태에서 실행).

사용: python verify.py --package <zip|dir> [--target-profile <json>] [--install-root DIR] [--json]
검사: manifest 필수 필드/형식, 파일 수/크기/hash (checksums.sha256 과 manifest.files 양쪽), 경로 탈출/symlink/ZIP 안전성,
      lock 텍스트(URL/VCS/editable/index 옵션 거부), wheelhouse ↔ lock 양방향 일치(앱 wheel 포함),
      대상 OS/Python/ABI/platform tag (현 호스트 또는 --target-profile), 디스크 여유, release ID 형식.
exit 0 = PASS, 5 = FAIL (E_PACKAGE_INVALID).
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="반입 패키지 검증 (표준 라이브러리만)")
    ap.add_argument("--package", required=True, help="ZIP 파일 또는 추출된 폴더")
    ap.add_argument(
        "--target-profile", default=None, help="검사 대상 target profile JSON (기본: 현 호스트 감지)"
    )
    ap.add_argument(
        "--install-root",
        default=None,
        help="디스크 여유 검사 위치 (기본: DIA_INSTALL_ROOT 또는 기본 설치 root)",
    )
    ap.add_argument("--json", action="store_true", help="결과 JSON 출력")
    args = ap.parse_args(argv)
    try:
        target = None
        if args.target_profile:
            target = c.read_json(args.target_profile)
            if not isinstance(target, dict):
                raise c.ScriptError("E_PACKAGE_INVALID", "target profile JSON 형식 오류")
        install_root = Path(args.install_root).expanduser() if args.install_root else c.default_install_root()
        with c.PackageSource(args.package) as src:
            report = c.verify_package(src, target=target, install_root=install_root)
    except c.ScriptError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2))
        else:
            print(exc.format_ko(), file=sys.stderr)
        return exc.exit_code
    if args.json:
        slim = {k: v for k, v in report.items() if k not in ("hashes", "wheels", "lock_entries")}
        slim["wheel_count"] = len(report.get("wheels", []))
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
    else:
        print(c.format_verify_report_ko(report))
        if not report["ok"]:
            print(
                "해결 방법: 실패 항목을 확인하세요. hash/파일 불일치는 전송 손상 가능성이 있으니 ZIP 의 SHA-256 을 다시 대조하고 다시 받으세요. target 불일치면 target_profile.min.json 을 빌드 담당에게 전달하세요.",
                file=sys.stderr,
            )
    return c.EXIT_OK if report["ok"] else c.EXIT_VALIDATION


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
