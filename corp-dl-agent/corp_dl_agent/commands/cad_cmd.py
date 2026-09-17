"""cad 명령: snapshot 수입(CSV/JSON) 과 live 추출(adapter 상태 표시).

- cad import --input <csv|json> --output <snapshot.json> [--mapping k=v ...] [--mass-output mass_result.json]
- cad extract --adapter catia_v5_com|3dexperience  (개인 PC/미설치 환경: E_NOT_SUPPORTED, 상태 INACTIVE)
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.common import Status, StatusRecord, atomic_write_json, now_iso
from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace

LIVE_ADAPTERS: dict[str, str] = {
    "catia_v5_com": "CATIA V5 Windows Automation (COM)",
    "3dexperience": "3DEXPERIENCE",
}


def approved_roots(cfg: AppConfig, *, for_output: bool = False) -> list[Path]:
    """입력/출력 경로 검사용 승인 root: 설정 경로 + 현재 작업 폴더."""
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.company_root, Path.cwd().resolve()]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    if for_output:
        roots.append(ws.outputs)
    return roots


def check_input_path(cfg: AppConfig, path: str) -> Path:
    return resolve_within(path, approved_roots(cfg), forbid_links=cfg.security.forbid_symlinks)


def check_output_path(cfg: AppConfig, path: str) -> Path:
    p = Path(path).expanduser()
    parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
    resolved_parent = resolve_within(
        parent, approved_roots(cfg, for_output=True), forbid_links=cfg.security.forbid_symlinks
    )
    resolved_parent.mkdir(parents=True, exist_ok=True)
    return resolved_parent / p.name


def load_cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)


def parse_mapping(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise AgentError("E_USAGE", f"--mapping 형식은 <snapshot필드>=<회사열이름> 이어야 합니다: {p}")
        k, v = p.split("=", 1)
        if not k.strip() or not v.strip():
            raise AgentError("E_USAGE", f"--mapping 의 필드/열 이름이 비어 있습니다: {p}")
        out[k.strip()] = v.strip()
    return out


def emit(args: argparse.Namespace, payload: dict[str, Any], text_lines: list[str]) -> None:
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in text_lines:
            print(line)


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("cad", help="CAD snapshot 수입(CSV/JSON) 및 live 추출 상태")
    sp = p.add_subparsers(dest="cad_command", metavar="<하위명령>")

    imp = sp.add_parser(
        "import", help="CSV(UTF-8/UTF-8-SIG/CP949) 또는 JSON snapshot 을 검증하여 cad_snapshot.json 으로 저장"
    )
    add_common_arguments(imp)
    imp.add_argument(
        "--input", required=True, help="입력 snapshot 파일 (.csv/.tsv/.json). 원본은 수정하지 않습니다"
    )
    imp.add_argument("--output", required=True, help="출력 cad_snapshot.json 경로")
    imp.add_argument(
        "--mapping",
        action="append",
        default=[],
        metavar="필드=열이름",
        help="회사 export 열 이름 매핑 (예: --mapping volume=Volume_mm3). config cad.field_mapping 과 병합",
    )
    imp.add_argument("--delimiter", default=",", help="CSV 구분자 (기본 ','; .tsv 는 탭)")
    imp.add_argument(
        "--lenient",
        action="store_true",
        help="오류 행이 있어도 issues 와 함께 저장 (기본은 E_INPUT_INVALID 로 거부)",
    )
    imp.add_argument(
        "--mass-output",
        default=None,
        help="지정 시 기본 MassScope 로 질량을 계산하여 mass_result.json 도 저장",
    )
    imp.set_defaults(handler=run_import)

    ext = sp.add_parser(
        "extract", help="CATIA/3DEXPERIENCE live 추출 (설치·COM·라이선스가 확인된 환경에서만)"
    )
    add_common_arguments(ext)
    ext.add_argument("--adapter", required=True, choices=sorted(LIVE_ADAPTERS), help="live adapter 종류")
    ext.add_argument("--output", default=None, help="출력 cad_snapshot.json 경로")
    ext.set_defaults(handler=run_extract)

    p.set_defaults(handler=lambda args: _print_cad_help(p))


def _print_cad_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def run_import(args: argparse.Namespace) -> int:
    from corp_dl_agent.engineering.cad_snapshot import import_snapshot, summarize_snapshot, write_snapshot

    cfg = load_cfg(args)
    mapping = dict(cfg.cad.field_mapping)
    mapping.update(parse_mapping(args.mapping))
    in_path = check_input_path(cfg, args.input)
    out_path = check_output_path(cfg, args.output)
    snapshot = import_snapshot(
        in_path, field_mapping=mapping or None, strict=not args.lenient, delimiter=args.delimiter
    )
    write_snapshot(snapshot, out_path)
    summary = summarize_snapshot(snapshot)
    payload: dict[str, Any] = {
        "ok": True,
        "output": str(out_path),
        "summary": summary,
        "issues": [i.model_dump() for i in snapshot.issues],
    }
    lines = [
        f"snapshot 저장: {out_path}",
        f"  행 {summary['n_rows']}개, reference {summary['n_references']}개, assembly {summary['n_assemblies']}개, 인코딩 {summary['encoding']}",
        f"  revision: {', '.join(summary['revisions'])} / geometry: {summary['geometry_status_counts']}",
        f"  issues: error {summary['issues']['error']}, warning {summary['issues']['warning']}, info {summary['issues']['info']}",
        f"  synthetic: {summary['synthetic']}",
    ]
    if args.mass_output:
        from corp_dl_agent.engineering.mass import compute_mass, load_material_table, mass_summary

        table = (
            load_material_table(str(check_input_path(cfg, cfg.cad.material_table)))
            if cfg.cad.material_table
            else None
        )
        result = compute_mass(
            snapshot,
            density_policy=cfg.cad.default_density_policy,
            material_table=table,
            material_table_path=cfg.cad.material_table,
        )
        mass_path = check_output_path(cfg, args.mass_output)
        atomic_write_json(mass_path, result.model_dump(mode="json"))
        ms = mass_summary(result)
        payload["mass_output"] = str(mass_path)
        payload["mass"] = ms
        lines.append(f"mass_result 저장: {mass_path}")
        lines.append(
            f"  총 질량 {ms['total_kg']:.6g} kg (complete={ms['complete']}, 누락 {len(ms['missing'])}건)"
        )
    emit(args, payload, lines)
    return 0


def live_adapter_status(adapter: str) -> StatusRecord:
    """live adapter 의 현재 상태. 설치/COM/라이선스가 확인되지 않으면 항상 INACTIVE(BLOCKED)."""
    label = LIVE_ADAPTERS.get(adapter, adapter)
    reasons: list[str] = []
    if platform.system() != "Windows":
        reasons.append(f"{platform.system()} 에서는 {label} 연결을 지원하지 않습니다 (Windows 전용)")
    else:
        reasons.append(f"{label} 설치/COM 등록/라이선스가 이 환경에서 확인되지 않았습니다")
    try:
        cad_adapters = importlib.import_module("corp_dl_agent.adapters.cad")  # 선택 모듈 (다른 소유자)
    except Exception:  # noqa: BLE001 - adapter 모듈 부재/오류는 진단만 하고 계속
        reasons.append("live adapter 모듈(adapters.cad)을 로드할 수 없습니다")
    else:
        cls_name = {"catia_v5_com": "CatiaV5ComAdapter", "3dexperience": "ThreeDExperienceAdapter"}.get(
            adapter
        )
        cls = getattr(cad_adapters, cls_name, None) if cls_name else None
        if cls is None:
            reasons.append("adapters.cad 에 해당 adapter 계약이 없습니다")
        else:
            try:
                available = bool(cls().available())
            except Exception:  # noqa: BLE001
                available = False
            if not available:
                reasons.append("adapter.available() = False")
    return StatusRecord(
        status=Status.BLOCKED, reason="INACTIVE: " + "; ".join(reasons), evidence=[], checked_at=now_iso()
    )


def run_extract(args: argparse.Namespace) -> int:
    load_cfg(args)  # 설정 검증 (경로/프로파일)
    status = live_adapter_status(args.adapter)
    raise AgentError(
        "E_NOT_SUPPORTED",
        f"'{args.adapter}' live 추출은 이 환경에서 지원되지 않습니다. CSV/JSON export 를 'cad import' 로 수입하세요.",
        hint="CATIA/3DEXPERIENCE 가 설치된 사내 Windows PC 에서 adapter 계약을 확인한 뒤 활성화합니다. 개인 PC 에서는 실제 연결을 수행하지 않습니다.",
        details={
            "adapter": args.adapter,
            "adapter_label": LIVE_ADAPTERS[args.adapter],
            "status": "INACTIVE",
            "status_record": status.model_dump(mode="json"),
            "fallback": "cad import --input <export.csv|json> --output cad_snapshot.json",
        },
    )
