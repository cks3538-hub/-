"""design 명령: 질량 계산, 원가 계산, 설계안 비교.

- design mass --snapshot cad_snapshot.json --output mass_result.json [--scope scope.json] [--material-table csv]
- design cost --mass mass_result.json --recipes cost_recipes.json --recipe <이름> --output cost_breakdown.json [--occurrence <path>]
- design compare --candidate A=<dir> --candidate B=<dir> --output design_comparison.json [--baseline A] [--allow-revision-mismatch]
"""

from __future__ import annotations

import argparse

from pydantic import ValidationError

from corp_dl_agent.cli import add_common_arguments
from corp_dl_agent.commands.cad_cmd import check_input_path, check_output_path, emit, load_cfg
from corp_dl_agent.common import atomic_write_json, read_json, validate_strict
from corp_dl_agent.errors import AgentError


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("design", help="질량/원가 계산 및 설계안(A/B/C) 비교")
    sp = p.add_subparsers(dest="design_command", metavar="<하위명령>")

    m = sp.add_parser("mass", help="cad_snapshot.json 으로 질량 계산 (kg = m3 × kg/m3) → mass_result.json")
    add_common_arguments(m)
    m.add_argument("--snapshot", required=True, help="cad import 로 만든 cad_snapshot.json")
    m.add_argument("--output", required=True, help="출력 mass_result.json")
    m.add_argument("--scope", default=None, help="MassScope JSON (도장/폼/접착제/구매품 포함 범위와 명시 kg 항목)")
    m.add_argument("--material-table", default=None, help="재료표 CSV (config cad.material_table 대신). 정책이 use_material_table 일 때만 사용")
    m.set_defaults(handler=run_mass)

    c = sp.add_parser("cost", help="mass_result.json 과 recipe 로 단위 원가 계산 → cost_breakdown.json")
    add_common_arguments(c)
    c.add_argument("--mass", required=True, help="mass_result.json")
    c.add_argument("--recipes", required=True, help="cost_recipes.json")
    c.add_argument("--recipe", required=True, help="recipes 안의 recipe 이름")
    c.add_argument("--output", required=True, help="출력 cost_breakdown.json")
    c.add_argument("--occurrence", default=None, help="특정 occurrence_path 의 단위 질량 기준으로 계산 (기본: 총 질량)")
    c.add_argument("--currency", default=None, help="기대 통화 (recipe 와 다르면 E_REVISION_MISMATCH)")
    c.add_argument("--base-date", default=None, help="기대 기준일 YYYY-MM-DD (recipe 와 다르면 E_REVISION_MISMATCH)")
    c.set_defaults(handler=run_cost)

    cp = sp.add_parser("compare", help="후보 폴더(A/B/C)의 mass/cost/performance/constraint 비교 → design_comparison.json")
    add_common_arguments(cp)
    cp.add_argument(
        "--candidate",
        action="append",
        required=True,
        metavar="ID=<폴더>",
        help="후보 폴더 (cad_snapshot.json 필수, mass_result.json/cost_breakdown.json/candidate.json 선택). 2개 이상",
    )
    cp.add_argument("--output", required=True, help="출력 design_comparison.json")
    cp.add_argument("--baseline", default=None, help="기준 후보 ID (기본: 첫 번째)")
    cp.add_argument(
        "--allow-revision-mismatch",
        action="store_true",
        help="revision/기준일/통화 불일치를 오류 대신 경고로 기록 (consistent=false)",
    )
    cp.set_defaults(handler=run_compare)

    p.set_defaults(handler=lambda args: _print_help(p))


def _print_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 2


def run_mass(args: argparse.Namespace) -> int:
    from corp_dl_agent.engineering.cad_snapshot import load_snapshot
    from corp_dl_agent.engineering.mass import MassScope, compute_mass, load_material_table, mass_summary

    cfg = load_cfg(args)
    snap = load_snapshot(check_input_path(cfg, args.snapshot))
    scope = MassScope()
    if args.scope:
        try:
            scope = validate_strict(MassScope, read_json(check_input_path(cfg, args.scope)))
        except ValidationError as exc:
            raise AgentError("E_SCHEMA_INVALID", "MassScope JSON 이 올바르지 않습니다", details={"error": str(exc)[:500]}) from exc
    table_path = args.material_table or cfg.cad.material_table
    table = load_material_table(str(check_input_path(cfg, table_path))) if table_path else None
    result = compute_mass(
        snap,
        scope=scope,
        density_policy=cfg.cad.default_density_policy,
        material_table=table,
        material_table_path=table_path,
    )
    out = check_output_path(cfg, args.output)
    atomic_write_json(out, result.model_dump(mode="json"))
    ms = mass_summary(result)
    lines = [
        f"mass_result 저장: {out}",
        f"  총 질량 {ms['total_kg']:.6g} kg (모델링 부품 {ms['total_modeled_kg']:.6g} kg, instance {ms['n_instances_counted']}개)",
        f"  complete={ms['complete']}, assumption={ms['assumption']}, 상태별: {ms['counts']}",
    ]
    lines += [f"  누락: {m}" for m in ms["missing"]]
    lines += [f"  경고: {w}" for w in ms["warnings"]]
    lines += [f"  범위: {v}" for v in ms["scope"].values()]
    emit(args, {"ok": True, "output": str(out), "mass": ms}, lines)
    return 0


def run_cost(args: argparse.Namespace) -> int:
    from corp_dl_agent.engineering.cost import compute_cost, cost_summary, load_recipes
    from corp_dl_agent.engineering.mass import MassResult

    cfg = load_cfg(args)
    mass_path = check_input_path(cfg, args.mass)
    try:
        mass = validate_strict(MassResult, read_json(mass_path))
    except ValidationError as exc:
        raise AgentError("E_SCHEMA_INVALID", f"mass_result.json 이 schema 와 일치하지 않습니다: {mass_path.name}", details={"error": str(exc)[:500]}) from exc
    recipes = load_recipes(check_input_path(cfg, args.recipes))
    if args.recipe not in recipes:
        raise AgentError("E_INPUT_INVALID", f"recipe '{args.recipe}' 가 없습니다", details={"available": sorted(recipes)})
    subject = mass.item(args.occurrence) if args.occurrence else mass
    breakdown = compute_cost(subject, recipes[args.recipe], expected_currency=args.currency, expected_base_date=args.base_date)
    out = check_output_path(cfg, args.output)
    atomic_write_json(out, breakdown.model_dump(mode="json"))
    cs = cost_summary(breakdown)
    uc = cs["unit_cost"]
    lines = [
        f"cost_breakdown 저장: {out}",
        f"  recipe {cs['recipe']} ({cs['recipe_type']}, {cs['estimate_type']}), 기준 {cs['subject']}",
        f"  단위 원가: {('MISSING' if uc is None else f'{uc:,.2f}')} {cs['currency']}/unit (기준일 {cs['base_date']}, complete={cs['complete']})",
    ]
    lines += [f"  - {k}: {('MISSING' if v is None else f'{v:,.2f}')}" for k, v in cs["lines"].items()]
    lines += [f"  가정: {a}" for a in cs["assumptions"]]
    lines += [f"  누락: {m}" for m in cs["missing"]]
    emit(args, {"ok": True, "output": str(out), "cost": cs}, lines)
    return 0


def _parse_candidates(specs: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for s in specs:
        if "=" not in s:
            raise AgentError("E_USAGE", f"--candidate 형식은 ID=<폴더> 이어야 합니다: {s}")
        cid, d = s.split("=", 1)
        if not cid.strip() or not d.strip():
            raise AgentError("E_USAGE", f"--candidate 의 ID/폴더가 비어 있습니다: {s}")
        out.append((cid.strip(), d.strip()))
    return out


def run_compare(args: argparse.Namespace) -> int:
    from corp_dl_agent.engineering.compare import compare_designs, comparison_summary, load_candidate_dir

    cfg = load_cfg(args)
    specs = _parse_candidates(args.candidate)
    if len(specs) < 2:
        raise AgentError("E_USAGE", "--candidate 는 2개 이상 지정해야 합니다")
    candidates = []
    for cid, d in specs:
        cdir = check_input_path(cfg, d)
        candidates.append(load_candidate_dir(cdir, candidate_id=cid))
    cmp = compare_designs(candidates, require_same_revision_policy=not args.allow_revision_mismatch, baseline_id=args.baseline)
    out = check_output_path(cfg, args.output)
    atomic_write_json(out, cmp.model_dump(mode="json"))
    summ = comparison_summary(cmp)
    lines = [
        f"design_comparison 저장: {out}",
        f"  기준 {summ['baseline']}, revision {summ['revision']}, 기준일 {summ['base_date']}, 통화 {summ['currency']}, consistent={summ['consistent']}, complete={summ['complete']}",
    ]
    for r in summ["rows"]:
        mass_s = "MISSING" if r["mass_kg"] is None else f"{r['mass_kg']:.6g} kg"
        cost_s = "MISSING" if r["unit_cost"] is None else f"{r['unit_cost']:,.2f}"
        lines.append(
            f"  [{r['id']}] {r['label']}: 질량 {mass_s} (complete={r['mass_complete']}), 원가 {cost_s} ({r['estimate_type']}), 미확인 {r['unknowns']}건"
        )
    for d in summ["deltas"]:
        dv = "MISSING" if d["delta"] is None else f"{d['delta']:+.6g} {d['unit']}"
        lines.append(f"  Δ {d['metric']} {d['candidate']} vs {summ['baseline']}: {dv}")
    lines += [f"  경고: {w}" for w in summ["warnings"]]
    emit(args, {"ok": True, "output": str(out), "comparison": summ}, lines)
    return 0

