"""docs 명령: PPTX/XLSX 색인·근거 검색·report_payload 기반 문서 생성.

- docs index    --roots <폴더> [...]            허용 root 만 색인 (sources_roots 가 설정되어 있으면 그 안의 폴더만)
- docs search   --query "..." [--evidence-output evidence.json]
- docs generate --payload report_payload.json --pptx-template ... --xlsx-template ... --output-dir ...
                → review.pptx, comparison.xlsx, evidence.json, report_payload.json, document_manifest.json, validation_report.json
- docs make-synthetic --output-dir <폴더>       합성 fixture 문서 생성 (synthetic 표시)

python-pptx/openpyxl 은 실제 실행 시점에만 import 한다 (없으면 E_BLOCKED_DEPENDENCY).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from corp_dl_agent.cli import add_common_arguments, parse_overrides
from corp_dl_agent.common import atomic_write_json, now_iso, sha256_file
from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.errors import EXIT_VALIDATION, AgentError
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace


def load_cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(args.config_path, parse_overrides(args.overrides), profile=args.profile)


def _input_roots(cfg: AppConfig) -> list[Path]:
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.company_root, Path.cwd().resolve()]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
    roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    return roots


def _output_roots(cfg: AppConfig) -> list[Path]:
    ws = Workspace.from_config(cfg)
    roots: list[Path] = [ws.data_root, ws.outputs, Path.cwd().resolve()]
    if cfg.paths.output_root:
        roots.append(Path(cfg.paths.output_root).expanduser().resolve())
    return roots


def check_input_path(cfg: AppConfig, path: str) -> Path:
    p = resolve_within(path, _input_roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"입력 파일이 없습니다: {p.name}", details={"path": str(p)})
    return p


def check_output_dir(cfg: AppConfig, path: str) -> Path:
    p = Path(path).expanduser()
    parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = resolve_within(parent, _output_roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    out = resolved_parent / p.name
    out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_db_path(cfg: AppConfig, db: str | None) -> Path:
    ws = Workspace.from_config(cfg)
    if not db:
        ws.indexes.mkdir(parents=True, exist_ok=True)
        return ws.index_db
    p = Path(db).expanduser()
    parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = resolve_within(parent, _output_roots(cfg), forbid_links=cfg.security.forbid_symlinks)
    return resolved_parent / p.name


def resolve_index_roots(cfg: AppConfig, roots_arg: list[str]) -> list[Path]:
    """CLI --roots 와 설정 sources_roots 의 교집합 규칙.

    - sources_roots 가 설정되어 있으면: --roots 가 없으면 sources_roots 전체, 있으면 그 하위 폴더만 허용.
    - sources_roots 가 비어 있으면(개인 개발): --roots 가 필수이며 명시된 폴더가 승인 root 가 된다.
    """
    requested: list[str] = []
    for r in roots_arg:
        requested.extend(x.strip() for x in r.split(",") if x.strip())
    approved = [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
    if not requested:
        if not approved:
            raise AgentError(
                "E_USAGE",
                "색인할 폴더가 없습니다. --roots <폴더> 를 지정하거나 설정 paths.sources_roots 를 채우세요.",
            )
        return approved
    out: list[Path] = []
    for r in requested:
        if approved:
            out.append(resolve_within(r, approved, forbid_links=cfg.security.forbid_symlinks))
        else:
            p = Path(r).expanduser().resolve()
            if not p.is_dir():
                raise AgentError(
                    "E_PATH_OUTSIDE_ROOT", f"색인 root 폴더가 없습니다: {p}", details={"root": str(p)}
                )
            out.append(p)
    return out


def emit(args: argparse.Namespace, payload: dict[str, Any], text_lines: list[str]) -> None:
    if getattr(args, "json_output", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in text_lines:
            print(line)


# ---------------------------------------------------------------------------
# 등록
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("docs", help="PPTX/XLSX 색인·근거 검색·report_payload 기반 문서 생성")
    sp = p.add_subparsers(dest="docs_command", metavar="<하위명령>")

    ix = sp.add_parser(
        "index", help="허용 폴더의 PPTX/XLSX 를 색인 (slide/shape/table/notes/chart, sheet/cell/named range)"
    )
    add_common_arguments(ix)
    ix.add_argument(
        "--roots",
        action="append",
        default=[],
        metavar="폴더",
        help="색인할 폴더 (반복 또는 콤마 구분). 설정 paths.sources_roots 가 있으면 그 하위만 허용",
    )
    ix.add_argument(
        "--db", default=None, help="색인 sqlite 경로 (기본: <data_root>/indexes/documents.sqlite)"
    )
    ix.add_argument("--no-prune", action="store_true", help="삭제된 파일을 색인에서 제거하지 않음")
    ix.add_argument(
        "--exclude-hidden", action="store_true", help="숨김 slide/shape/sheet/행 내용을 아예 저장하지 않음"
    )
    ix.add_argument(
        "--max-file-mb", type=int, default=None, help="파일 크기 상한 MB (기본: documents.max_index_file_mb)"
    )
    ix.set_defaults(handler=run_index)

    se = sp.add_parser(
        "search", help="키워드/metadata 검색 (embedding 없음). 숨김 내용은 --include-hidden 없이는 제외"
    )
    add_common_arguments(se)
    se.add_argument("--query", required=True, help='검색어 (공백 구분, "구절" 가능)')
    se.add_argument("--limit", type=int, default=20, help="최대 결과 수 (기본 20)")
    se.add_argument("--include-hidden", action="store_true", help="숨김 콘텐츠 포함 (명시적 허용)")
    se.add_argument(
        "--kind",
        choices=["fact", "form_reference"],
        default=None,
        help="fact(현재 근거) / form_reference(과거 양식) 만",
    )
    se.add_argument("--db", default=None, help="색인 sqlite 경로")
    se.add_argument("--evidence-output", default=None, help="결과를 evidence.json 으로 저장할 경로")
    se.add_argument("--purpose", default="docs search", help="evidence 목적 문구")
    se.set_defaults(handler=run_search)

    ge = sp.add_parser(
        "generate",
        help="report_payload.json 으로 템플릿 복사본을 채워 review.pptx / comparison.xlsx / evidence / manifest / validation_report 생성",
    )
    add_common_arguments(ge)
    ge.add_argument("--payload", required=True, help="report_payload.json 경로 (수치 단일 원본)")
    ge.add_argument("--pptx-template", default=None, help="{{key}} placeholder 가 있는 PPTX 템플릿")
    ge.add_argument("--xlsx-template", default=None, help="named range 입력 셀이 있는 XLSX 템플릿")
    ge.add_argument("--output-dir", required=True, help="산출물 폴더")
    ge.add_argument(
        "--query",
        action="append",
        default=[],
        help="근거 검색어 (반복 가능). 색인에서 검색하여 evidence.json 생성",
    )
    ge.add_argument("--evidence", default=None, help="이미 만든 evidence.json 을 그대로 사용")
    ge.add_argument("--db", default=None, help="색인 sqlite 경로 (--query 사용 시)")
    ge.add_argument(
        "--include-hidden", action="store_true", help="근거 검색에 숨김 콘텐츠 포함 (명시적 허용)"
    )
    ge.add_argument(
        "--allow-lossy-xlsx",
        action="store_true",
        help="openpyxl 이 보존 못하는 요소(차트/그림/피벗/OLE)가 있어도 호환성 보고와 함께 진행",
    )
    ge.add_argument(
        "--stale",
        action="append",
        default=[],
        metavar="값",
        help="잔존 검사할 오래된 값 추가 (payload.stale_values 에 더함)",
    )
    ge.add_argument("--pptx-name", default="review.pptx", help="PPTX 산출물 파일명 (기본 review.pptx)")
    ge.add_argument(
        "--xlsx-name", default="comparison.xlsx", help="XLSX 산출물 파일명 (기본 comparison.xlsx)"
    )
    ge.set_defaults(handler=run_generate)

    ms = sp.add_parser(
        "make-synthetic",
        help="합성 fixture 문서 생성 (past_review/past_cost_table/템플릿/예시 payload, synthetic 표시)",
    )
    add_common_arguments(ms)
    ms.add_argument("--output-dir", required=True, help="생성 폴더")
    ms.set_defaults(handler=run_make_synthetic)

    p.set_defaults(handler=lambda args: _print_help(p))


def _print_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def run_index(args: argparse.Namespace) -> int:
    from corp_dl_agent.documents.index import DocumentIndex

    cfg = load_cfg(args)
    roots = resolve_index_roots(cfg, args.roots)
    db_path = resolve_db_path(cfg, args.db)
    with DocumentIndex(db_path) as index:
        report = index.index_roots(
            roots,
            include_hidden_flag=not args.exclude_hidden,
            include_notes=cfg.documents.include_notes,
            max_file_mb=args.max_file_mb or cfg.documents.max_index_file_mb,
            prune=not args.no_prune,
            forbid_links=cfg.security.forbid_symlinks,
        )
        stats = index.stats()
    payload = {"ok": True, "db": str(db_path), "report": report.model_dump(mode="json"), "stats": stats}
    lines = [
        f"색인 DB: {db_path}",
        f"  root: {', '.join(report.roots)}",
        f"  신규/변경 {len(report.indexed)}개, 변경 없음 {len(report.unchanged)}개, 제거 {len(report.pruned)}개",
        f"  승인 변환 필요(.ppt/.xls) {len(report.needs_conversion)}개, 크기 초과 {len(report.skipped)}개, 오류 {len(report.errors)}개",
        f"  문서 {stats['n_documents']}개, fragment {stats['n_fragments']}개 (숨김 {stats['n_hidden_fragments']}개는 검색 기본 제외)",
    ]
    for path, flags in report.flagged.items():
        lines.append(f"  flag {Path(path).name}: {', '.join(flags)} (실행하지 않음)")
    for e in report.errors:
        lines.append(f"  오류 {Path(e['path']).name}: {e['reason']}")
    emit(args, payload, lines)
    return 0


def run_search(args: argparse.Namespace) -> int:
    from corp_dl_agent.documents.evidence import build_evidence, write_evidence
    from corp_dl_agent.documents.index import DocumentIndex
    from corp_dl_agent.documents.search import search

    cfg = load_cfg(args)
    db_path = resolve_db_path(cfg, args.db)
    if not db_path.is_file():
        raise AgentError(
            "E_INPUT_INVALID", f"색인 DB 가 없습니다: {db_path.name}. 먼저 docs index 를 실행하세요."
        )
    include_hidden = bool(args.include_hidden)
    with DocumentIndex(db_path) as index:
        hits = search(index, args.query, limit=args.limit, include_hidden=include_hidden, kind=args.kind)
    payload: dict[str, Any] = {
        "ok": True,
        "query": args.query,
        "n_hits": len(hits),
        "include_hidden": include_hidden,
        "hits": [h.model_dump(mode="json") for h in hits],
    }
    lines = [f"검색어: {args.query} → {len(hits)}건 (숨김 {'포함' if include_hidden else '제외'})"]
    for h in hits:
        flag = " [hidden]" if h.hidden else ""
        extra = f" formula={h.formula} cached={h.cached_value}" if h.formula else ""
        lines.append(f"  {h.score:5.2f} {h.kind:14s} {Path(h.path).name} {h.locator}{flag}{extra}")
        lines.append(f"        {h.text[:100].replace(chr(10), ' / ')}")
    if args.evidence_output:
        ev = build_evidence(
            hits,
            purpose=args.purpose,
            query=args.query,
            allow_hidden=include_hidden and cfg.documents.allow_hidden_content_to_llm,
        )
        out = check_output_dir(cfg, str(Path(args.evidence_output).parent)) / Path(args.evidence_output).name
        write_evidence(ev, out)
        payload["evidence_output"] = str(out)
        payload["evidence"] = ev.model_dump(mode="json")
        lines.append(f"evidence 저장: {out} ({ev.n_items}건, 숨김 제외 {ev.excluded_hidden}건)")
    emit(args, payload, lines)
    return 0


def run_generate(args: argparse.Namespace) -> int:
    from corp_dl_agent.documents.evidence import Evidence, build_evidence, load_evidence, write_evidence
    from corp_dl_agent.documents.payload import load_payload, validate_payload, write_payload
    from corp_dl_agent.documents.templates import (
        DocumentManifest,
        GenerationManifest,
        fill_pptx,
        fill_xlsx,
        write_generation_manifest,
    )
    from corp_dl_agent.documents.validation import validate_documents, write_validation_report

    cfg = load_cfg(args)
    if not args.pptx_template and not args.xlsx_template:
        raise AgentError("E_USAGE", "--pptx-template 또는 --xlsx-template 중 하나 이상이 필요합니다.")
    payload_path = check_input_path(cfg, args.payload)
    out_dir = check_output_dir(cfg, args.output_dir)
    payload = load_payload(payload_path)
    if args.stale:
        payload = payload.model_copy(update={"stale_values": [*payload.stale_values, *args.stale]})
    pv = validate_payload(payload)
    validated = pv.payload
    payload_out = write_payload(validated, out_dir / "report_payload.json")

    # evidence
    evidence: Evidence
    if args.evidence:
        evidence = load_evidence(check_input_path(cfg, args.evidence))
    elif args.query:
        from corp_dl_agent.documents.index import DocumentIndex
        from corp_dl_agent.documents.search import search

        db_path = resolve_db_path(cfg, args.db)
        if not db_path.is_file():
            raise AgentError(
                "E_INPUT_INVALID", f"색인 DB 가 없습니다: {db_path.name}. 먼저 docs index 를 실행하세요."
            )
        include_hidden = bool(args.include_hidden)
        hits = []
        with DocumentIndex(db_path) as index:
            for q in args.query:
                hits.extend(search(index, q, limit=20, include_hidden=include_hidden))
        evidence = build_evidence(
            hits,
            purpose=f"report {validated.report_id}",
            query=" | ".join(args.query),
            allow_hidden=include_hidden and cfg.documents.allow_hidden_content_to_llm,
        )
    else:
        evidence = build_evidence([], purpose=f"report {validated.report_id}", query=None)
        evidence.notes.append("근거 검색어(--query)/evidence 파일이 지정되지 않아 빈 evidence 입니다.")
    evidence_path = write_evidence(evidence, out_dir / "evidence.json")

    manifests: list[DocumentManifest] = []
    pptx_out: Path | None = None
    xlsx_out: Path | None = None
    if args.pptx_template:
        tpl = check_input_path(cfg, args.pptx_template)
        pptx_out = out_dir / args.pptx_name
        manifests.append(fill_pptx(tpl, validated, pptx_out, include_notes=cfg.documents.include_notes))
    if args.xlsx_template:
        tpl = check_input_path(cfg, args.xlsx_template)
        xlsx_out = out_dir / args.xlsx_name
        manifests.append(fill_xlsx(tpl, validated, xlsx_out, allow_lossy=bool(args.allow_lossy_xlsx)))
    gen = GenerationManifest(
        created_at=now_iso(),
        payload_report_id=validated.report_id,
        payload_path=str(payload_out),
        payload_hash=sha256_file(payload_out),
        evidence_path=str(evidence_path),
        evidence_hash=sha256_file(evidence_path),
        documents=manifests,
        synthetic=validated.synthetic,
        notes=[
            "수식 재계산/렌더링은 수행하지 않았습니다 (RECALC_NOT_RUN / RENDER_NOT_RUN).",
            f"renderer={cfg.documents.renderer}, recalc_engine={cfg.documents.recalc_engine}",
        ],
    )
    manifest_path = write_generation_manifest(gen, out_dir / "document_manifest.json")
    report = validate_documents(
        pptx_out,
        xlsx_out,
        validated,
        manifests=manifests,
        payload_validation=pv,
        renderer=cfg.documents.renderer,
        recalc_engine=cfg.documents.recalc_engine,
    )
    report_path = write_validation_report(report, out_dir / "validation_report.json")
    summary_path = out_dir / "generate_summary.json"
    outputs = {
        "report_payload": str(payload_out),
        "evidence": str(evidence_path),
        "review_pptx": str(pptx_out) if pptx_out else None,
        "comparison_xlsx": str(xlsx_out) if xlsx_out else None,
        "document_manifest": str(manifest_path),
        "validation_report": str(report_path),
    }
    atomic_write_json(
        summary_path,
        {"ok": report.passed, "outputs": outputs, "synthetic": validated.synthetic, "created_at": now_iso()},
    )
    payload_json: dict[str, Any] = {
        "ok": report.passed,
        "outputs": outputs,
        "payload_validation": {
            "passed": pv.passed,
            "n_missing": pv.n_missing,
            "n_conflict": pv.n_conflict,
            "checks": [c.model_dump(mode="json") for c in pv.checks],
            "provenance_issues": pv.provenance_issues,
        },
        "validation": report.model_dump(mode="json"),
        "manifest": gen.model_dump(mode="json"),
    }
    lines = [f"산출물 폴더: {out_dir}"]
    for k, v in outputs.items():
        if v:
            lines.append(f"  {k}: {Path(v).name}")
    lines.append(
        f"payload 재검증: {'PASS' if pv.passed else 'FAIL'} (MISSING {pv.n_missing}, CONFLICT {pv.n_conflict}, 출처 문제 {len(pv.provenance_issues)})"
    )
    for m in manifests:
        lines.append(
            f"  {m.document_type}: 변경 {len(m.changed_locators)}곳, MISSING {len(m.missing_keys)}, 미지원 요소 {len(m.unsupported_elements)}, 템플릿 hash 불변 {m.template_hash_unchanged}, {m.recalc_status}/{m.render_status}"
        )
    for d in report.documents:
        lines.append(
            f"  검증 {d.document_type}: PASS {d.n_pass} / FAIL {d.n_fail} / NOT_RUN {d.n_not_run} / PARTIAL {d.n_partial}"
        )
        for c in d.checks:
            if c.status.value == "FAIL":
                lines.append(f"    FAIL {c.check_id}: {c.message_ko} ({c.locator or ''})")
    lines.append(
        f"evidence: {evidence.n_items}건 (fact {evidence.n_fact}, form_reference {evidence.n_form_reference}, 숨김 제외 {evidence.excluded_hidden})"
    )
    lines.append(f"synthetic: {validated.synthetic}")
    if not report.passed:
        lines.append(
            "검증 실패: validation_report.json 의 FAIL 항목을 확인하세요 (E_DOC_PAYLOAD_MISMATCH/E_DOC_STRUCTURE_CHANGED)."
        )
    emit(args, payload_json, lines)
    return 0 if report.passed else EXIT_VALIDATION


def run_make_synthetic(args: argparse.Namespace) -> int:
    from corp_dl_agent.documents.synthetic import make_synthetic_documents

    cfg = load_cfg(args)
    out_dir = check_output_dir(cfg, args.output_dir)
    files = make_synthetic_documents(out_dir)
    payload = {
        "ok": True,
        "output_dir": str(out_dir),
        "files": {k: str(v) for k, v in files.items()},
        "synthetic": True,
    }
    lines = [f"합성 문서 생성 (synthetic): {out_dir}"] + [f"  {k}" for k in files]
    emit(args, payload, lines)
    return 0
