from __future__ import annotations

import argparse
import json

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.config import load_config


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "self-test", help="설치 자체 검증 (단위 1.0 kg, sqlite, 경로, torch CPU, 문서 라이브러리)"
    )
    add_common_arguments(p)
    p.add_argument(
        "--no-ml", action="store_true", help="ml-cpu 패키지를 필수로 요구하지 않음 (core/documents 프로파일)"
    )
    p.add_argument("--no-documents", action="store_true", help="documents 패키지를 필수로 요구하지 않음")
    p.add_argument("--output", default=None, help="결과 JSON 저장 경로")
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    from corp_dl_agent.selftest import format_selftest_ko, run_selftest, write_selftest_result

    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    result = run_selftest(cfg, require_ml=not args.no_ml, require_documents=not args.no_documents)
    if args.output:
        write_selftest_result(result, args.output)
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_selftest_ko(result))
    return int(result["exit_code"])
