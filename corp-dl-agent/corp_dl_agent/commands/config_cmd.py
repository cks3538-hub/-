from __future__ import annotations

import argparse
import json

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.config import load_config, mask_config
from corp_dl_agent.errors import AgentError


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("config", help="설정 검증/표시")
    s = p.add_subparsers(dest="config_command", metavar="<하위명령>")
    v = s.add_parser("validate", help="회사 설정 YAML 을 strict schema 로 검증 (오류 위치 표시)")
    add_common_arguments(v)
    v.set_defaults(handler=run_validate)
    sh = s.add_parser("show", help="마스킹된 최종(resolved) 설정 표시")
    add_common_arguments(sh)
    sh.set_defaults(handler=run_show)
    p.set_defaults(handler=lambda args: (p.print_help(), 2)[1])


def run_validate(args: argparse.Namespace) -> int:
    if not args.config_path:
        raise AgentError("E_USAGE", "config validate 에는 --config <yaml> 이 필요합니다")
    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    if args.json_output:
        print(
            json.dumps(
                {"ok": True, "profile": cfg.profile.value, "config_path": args.config_path},
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"설정 검증 통과: {args.config_path} (profile={cfg.profile.value}, network_allowed={cfg.network_allowed()})"
        )
    return 0


def run_show(args: argparse.Namespace) -> int:
    cfg = load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)
    masked = mask_config(cfg.model_dump(mode="json"))
    print(json.dumps(masked, ensure_ascii=False, indent=2))
    return 0
