"""integrations 명령: 사내 gateway/Atlassian 설정 상태 진단과 원격 쓰기 preview.

- integrations doctor [--probe] [--json]
    gateway(LLM)·Atlassian 4종·outbound guard 상태. 기본은 연결하지 않는다 (NOT_RUN/BLOCKED 사유).
- integrations preview --product jira|confluence|bitbucket|bamboo --payload file.json [--output out.json] [--json]
    등록 전 미리보기(보낼 요청 dict) 만 생성한다. integrations.write=false(기본) 에서는 어떤 원격 쓰기도 하지 않는다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.common import atomic_write_json, read_json
from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace

PRODUCT_CHOICES = ("jira", "confluence", "bitbucket", "bamboo")


def load_cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)


def _roots(cfg: AppConfig, *, for_output: bool = False) -> list[Path]:
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.company_root, Path.cwd().resolve()]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    if for_output:
        roots.append(ws.outputs)
    return roots


def emit(args: argparse.Namespace, payload: dict[str, Any], text_lines: list[str]) -> None:
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in text_lines:
            print(line)


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "integrations", help="사내 gateway/Atlassian 연동 상태 진단 및 쓰기 preview (기본 원격 쓰기 off)"
    )
    sp = p.add_subparsers(dest="integrations_command", metavar="<하위명령>")

    d = sp.add_parser("doctor", help="gateway/Atlassian 설정 상태 (연결 없음, 값 마스킹)")
    add_common_arguments(d)
    d.add_argument(
        "--probe",
        action="store_true",
        help="설정된 제품에 읽기 전용 probe 요청 1회 (corp-gateway 프로파일 + approved origin 에서만)",
    )
    d.set_defaults(handler=run_doctor)

    pv = sp.add_parser("preview", help="원격 등록 전 미리보기 (요청 dict 만 생성, 네트워크 없음)")
    add_common_arguments(pv)
    pv.add_argument("--product", required=True, choices=PRODUCT_CHOICES, help="대상 제품")
    pv.add_argument(
        "--payload", required=True, help="payload JSON 파일 (예: jira: {summary, description, issue_type})"
    )
    pv.add_argument("--output", default=None, help="preview JSON 저장 경로 (선택)")
    pv.set_defaults(handler=run_preview)

    p.set_defaults(handler=lambda args: _print_help(p))


def _print_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def run_doctor(args: argparse.Namespace) -> int:
    from corp_dl_agent.adapters.atlassian import integrations_doctor
    from corp_dl_agent.adapters.cad import all_adapter_status
    from corp_dl_agent.adapters.llm import gateway_status
    from corp_dl_agent.security.network import OutboundGuard

    cfg = load_cfg(args)
    gw = gateway_status(cfg)
    atl = integrations_doctor(cfg, probe=bool(args.probe))
    guard = OutboundGuard.from_config(cfg).describe()
    cad = all_adapter_status()
    payload: dict[str, Any] = {
        "ok": True,
        "profile": cfg.profile.value,
        "network_allowed": cfg.network_allowed(),
        "gateway": gw.model_dump(mode="json"),
        "atlassian": atl,
        "outbound_guard": guard,
        "cad_adapters": cad,
        "live_verified": False,
    }
    lines = [
        f"프로파일: {cfg.profile.value} (네트워크 허용: {cfg.network_allowed()})",
        f"LLM gateway: {gw.status.value} - {gw.reason}",
        f"Atlassian 원격 쓰기: {'ON' if atl['write_enabled'] else 'OFF (preview 만)'}",
    ]
    for name, rec in atl["products"].items():
        lines.append(f"  - {name}: {rec['status']} - {rec['reason']}")
    lines.append(
        f"outbound guard: {'활성' if guard['active'] else '비활성'} (프로세스 내 socket 검사, OS 수준 격리 아님)"
    )
    for name, rec in cad.items():
        lines.append(f"CAD adapter {name}: {rec['status']['status']} - {rec['status']['reason']}")
    lines.append("live verified: 아니오 (개인 개발 단계에서는 mock 계약 시험까지만)")
    emit(args, payload, lines)
    return 0


def run_preview(args: argparse.Namespace) -> int:
    from corp_dl_agent.adapters.atlassian import preview_payload

    cfg = load_cfg(args)
    payload_path = resolve_within(args.payload, _roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    if not payload_path.is_file():
        raise AgentError("E_INPUT_INVALID", f"payload 파일이 없습니다: {payload_path.name}")
    try:
        data = read_json(payload_path)
    except ValueError as exc:
        raise AgentError("E_INPUT_INVALID", f"payload JSON 파싱 실패: {payload_path.name}") from exc
    if not isinstance(data, dict):
        raise AgentError("E_INPUT_INVALID", "payload JSON 최상위는 객체(dict) 이어야 합니다")
    preview = preview_payload(args.product, cfg, data)
    out_path: Path | None = None
    if args.output:
        p = Path(args.output).expanduser()
        parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
        resolved_parent = resolve_within(
            parent, _roots(cfg, for_output=True), forbid_links=cfg.security.forbid_symlinks
        )
        resolved_parent.mkdir(parents=True, exist_ok=True)
        out_path = resolved_parent / p.name
        atomic_write_json(out_path, preview)
    result: dict[str, Any] = {
        "ok": True,
        "product": args.product,
        "write_enabled": cfg.integrations.write,
        "performed": False,
        "preview": preview,
        "output": str(out_path) if out_path else None,
    }
    lines = [
        f"[{args.product}] preview ({preview['action']}) - 원격 쓰기 수행 안 함",
        f"  {preview['method']} {preview['url']}",
        f"  body: {json.dumps(preview['body'], ensure_ascii=False)[:500]}",
        f"  write_enabled={cfg.integrations.write}: {preview['note']}",
    ]
    if out_path:
        lines.append(f"  preview 저장: {out_path}")
    emit(args, result, lines)
    return 0
