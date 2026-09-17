from __future__ import annotations

import argparse
import json

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.config import load_config


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("doctor", help="환경/패키지/경로/프로파일/연결 상태 진단 (secret 마스킹)")
    add_common_arguments(p)
    p.add_argument("--no-paths", action="store_true", help="경로 쓰기 검사 생략")
    p.add_argument(
        "--export-diagnostics-preview",
        action="store_true",
        help="허용 필드만의 최소 진단 요약을 표시 (자동 반출 없음)",
    )
    p.add_argument("--output", default=None, help="진단 결과 JSON 저장 경로")
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    from corp_dl_agent.doctor import export_diagnostics_preview, format_doctor_ko, run_doctor

    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    result = run_doctor(cfg, check_paths=not args.no_paths)
    if args.output:
        from corp_dl_agent.common import atomic_write_json

        atomic_write_json(args.output, result)
    if args.export_diagnostics_preview:
        preview = export_diagnostics_preview(result)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_doctor_ko(result))
    return 0
