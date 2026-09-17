"""등록 도구(TOOL_REGISTRY) 와 dispatch.

LLM/offline planner 가 만들 수 있는 것은 아래 6 종의 등록 도구 호출 뿐이다.
- validate_input        TaskSpec 데이터 검증·고정 분할·누수 검사 (학습 없음)
- calculate_mass_cost   CAD snapshot 수입 -> 중량 -> (recipe 있으면) 원가
- train_model           기준 모델 + MLP 학습 (ml.runner 필요)
- predict               export 된 모델로 예측 (ml.predict 필요)
- search_documents      로컬 PPTX/XLSX 색인 검색
- generate_report       report_payload 로 PPTX/XLSX 생성 + 검증

규칙
- 입력은 strict 모델(extra 금지, 타입 강제, 범위) 로만 검증한다. 경로는 승인 root 안으로 제한한다.
- 실제 구현 모듈은 lazy import 하며 없으면 E_BLOCKED_DEPENDENCY 로 보고한다 (가짜 성공 없음).
- eval/exec/subprocess 로 LLM 출력을 실행하지 않는다. 도구 이름 -> Python 함수 매핑은 이 파일의 정적 표 뿐이다.
- 숫자는 코어 계산 엔진/metrics 만 만든다. 도구는 결과를 그대로 반환하며 값을 채우지 않는다.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from corp_dl_agent.common import Status, StrictModel, atomic_write_json, now_iso, validate_strict
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import AgentError
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.workspace import Workspace

MAX_PATH_CHARS = 1024
MAX_QUERY_CHARS = 500


# --------------------------------------------------------------------------- 입력 모델 (strict)


class ValidateInputArgs(StrictModel):
    taskspec_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    output_dir: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    lock_hash: str | None = Field(default=None, max_length=128)


class ScopeItemArgs(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    mass_kg: float = Field(ge=0)
    source_locator: str | None = Field(default=None, max_length=500)
    assumption: bool = False
    notes: str | None = Field(default=None, max_length=500)


class CalculateMassCostArgs(StrictModel):
    snapshot_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    output_dir: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    recipes_path: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    recipe_name: str | None = Field(default=None, max_length=200)
    material_table: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    density_policy: Literal["reject", "use_material_table"] | None = None
    expected_currency: str | None = Field(default=None, max_length=8)
    expected_base_date: str | None = Field(default=None, max_length=10)
    include_paint: bool = False
    include_foam: bool = False
    include_adhesive: bool = False
    include_purchased: bool = False
    paint_items: list[ScopeItemArgs] = Field(default_factory=list, max_length=200)
    foam_items: list[ScopeItemArgs] = Field(default_factory=list, max_length=200)
    adhesive_items: list[ScopeItemArgs] = Field(default_factory=list, max_length=200)
    purchased_items: list[ScopeItemArgs] = Field(default_factory=list, max_length=200)


class TrainModelArgs(StrictModel):
    """train_model 인자. mode 는 cfg.ml.demo / cfg.ml.pilot 예산을 고른다 (demo: 후보 2·10 epochs·300초 기본).

    max_candidates/max_epochs/patience/wall_time_seconds 를 주면 그 예산 안에서만 줄이거나 늘린다 (registry 상한 이내).
    """

    taskspec_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    mode: Literal["demo", "pilot"] = "demo"
    run_id: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    resume: bool = False
    device: Literal["cpu"] = "cpu"  # cuda 는 설정+드라이버 확인 후 CLI 에서만
    max_candidates: int | None = Field(default=None, ge=1, le=6)
    max_epochs: int | None = Field(default=None, ge=1, le=100)
    patience: int | None = Field(default=None, ge=1, le=100)
    wall_time_seconds: int | None = Field(default=None, ge=10, le=3600)
    lock_hash: str | None = Field(default=None, max_length=128)


class PredictArgs(StrictModel):
    model_dir: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    input_csv: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    output_csv: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    # synthetic 번들은 명시적으로만 허용 (운영 자동 채택 금지). False 면 cfg.ml.allow_synthetic_models_in_production 을 따른다.
    allow_synthetic: bool = False


class SearchDocumentsArgs(StrictModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    limit: int = Field(default=20, ge=1, le=100)
    include_hidden: bool = False
    roots: list[str] = Field(default_factory=list, max_length=20)  # 지정 시 검색 전에 색인 갱신
    index_db: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    kind: Literal["form_reference", "fact"] | None = None


class GenerateReportArgs(StrictModel):
    payload_path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    output_dir: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    pptx_template: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    xlsx_template: str | None = Field(default=None, max_length=MAX_PATH_CHARS)


# --------------------------------------------------------------------------- 도구 명세/컨텍스트


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[StrictModel]
    description_ko: str
    side_effects: tuple[str, ...]  # "reads_files" | "writes_files" | "trains_model" | "indexes_documents"
    requires: tuple[str, ...]  # lazy import 대상 모듈 (없으면 E_BLOCKED_DEPENDENCY)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description_ko": self.description_ko,
            "side_effects": list(self.side_effects),
            "requires": list(self.requires),
            "input_schema": self.input_model.model_json_schema(),
        }


@dataclass
class ToolContext:
    """dispatch 실행 컨텍스트: 설정, 승인 root, 작업 폴더. 네트워크 접근 정보는 포함하지 않는다."""

    cfg: AppConfig
    workspace: Workspace
    input_roots: list[Path]
    output_roots: list[Path]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: AppConfig, *, extra_roots: list[str | Path] | None = None) -> ToolContext:
        ws = Workspace.from_config(cfg)
        roots: list[Path] = [ws.data_root, ws.company_root, Path.cwd().resolve()]
        roots += [Path(p).expanduser().resolve() for p in cfg.paths.input_roots]
        roots += [Path(p).expanduser().resolve() for p in cfg.paths.sources_roots]
        if cfg.paths.output_root:
            roots.append(Path(cfg.paths.output_root).expanduser().resolve())
        roots += [Path(p).expanduser().resolve() for p in (extra_roots or [])]
        out_roots = [*roots, ws.outputs]
        return cls(cfg=cfg, workspace=ws, input_roots=roots, output_roots=out_roots)

    def resolve_input(self, path: str) -> Path:
        return resolve_within(path, self.input_roots, forbid_links=self.cfg.security.forbid_symlinks)

    def _resolve_output_parent(self, path: str) -> tuple[Path, str]:
        """출력 경로의 부모를 승인 root 안에서 확인한 뒤에만 폴더를 만든다 (검사 전 mkdir 금지)."""
        p = Path(path).expanduser()
        parent = p.parent if str(p.parent) not in ("", ".") else Path.cwd()
        resolved_parent = resolve_within(
            parent, self.output_roots, forbid_links=self.cfg.security.forbid_symlinks
        )
        resolved_parent.mkdir(parents=True, exist_ok=True)
        return resolved_parent, p.name

    def resolve_output_dir(self, path: str) -> Path:
        resolved_parent, name = self._resolve_output_parent(path)
        out = resolved_parent / name
        out.mkdir(parents=True, exist_ok=True)
        return out

    def resolve_output_file(self, path: str) -> Path:
        resolved_parent, name = self._resolve_output_parent(path)
        return resolved_parent / name


def _lazy(module: str, feature: str) -> Any:
    """실제 구현 모듈을 lazy import. 없으면 E_BLOCKED_DEPENDENCY (가짜 성공 금지)."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        from corp_dl_agent.errors import blocked_dependency

        err = blocked_dependency(module, feature)
        err.details["import_error"] = type(exc).__name__
        raise err from exc


def _attr(mod: Any, name: str, feature: str) -> Any:
    fn = getattr(mod, name, None)
    if fn is None:
        raise AgentError(
            "E_BLOCKED_DEPENDENCY",
            f"'{feature}' 기능에 필요한 '{mod.__name__}.{name}' 이(가) 아직 구현되어 있지 않습니다",
            details={"module": mod.__name__, "attribute": name, "feature": feature},
        )
    return fn


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    return obj


# --------------------------------------------------------------------------- 구현


def run_validate_input(args: ValidateInputArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "validate_input"
    ml_pkg = _lazy("corp_dl_agent.ml", feature)
    taskspec_mod = _lazy("corp_dl_agent.ml.taskspec", feature)
    data_mod = _lazy("corp_dl_agent.ml.data", feature)
    split_mod = _lazy("corp_dl_agent.ml.split", feature)
    prep_mod = _lazy("corp_dl_agent.ml.preprocessing", feature)

    ts_path = ctx.resolve_input(args.taskspec_path)
    spec = _attr(taskspec_mod, "load_taskspec", feature)(ts_path)
    out_dir = ctx.resolve_output_dir(
        args.output_dir or str(ctx.workspace.outputs / "validate" / spec.task_id)
    )
    roots = [*ctx.input_roots, ts_path.parent]
    df, report = _attr(data_mod, "load_dataset", feature)(
        spec, roots=roots, forbid_links=ctx.cfg.security.forbid_symlinks
    )
    atomic_write_json(out_dir / "data_report.json", _dump(report))
    code_hash = _attr(ml_pkg, "compute_code_hash", feature)()
    manifest = _attr(split_mod, "group_split", feature)(
        df, spec, code_hash=code_hash, lock_hash=args.lock_hash, data_hash=report.sha256
    )
    _attr(split_mod, "save_split", feature)(manifest, out_dir / "split_manifest.json")
    train, _val, _test = _attr(split_mod, "apply_split", feature)(df, manifest)
    findings = _attr(prep_mod, "check_leakage", feature)(train, spec)
    summary = {
        "tool": feature,
        "task_id": spec.task_id,
        "task_type": spec.task_type,
        "data_origin": spec.data_origin,
        "synthetic": spec.data_origin == "synthetic",
        "training_performed": False,
        "n_rows": report.n_rows,
        "n_groups": manifest.n_groups,
        "overlap_checked": manifest.overlap_checked,
        "leakage_findings": [_dump(f) for f in findings],
        "acceptance": "NEEDS_ACCEPTANCE_CRITERIA" if spec.acceptance is None else _dump(spec.acceptance),
        "status": Status.FAIL.value if findings else Status.PASS.value,
        "outputs": {
            "data_report": str(out_dir / "data_report.json"),
            "split_manifest": str(out_dir / "split_manifest.json"),
        },
        "created_at": now_iso(),
    }
    atomic_write_json(out_dir / "validate_input_summary.json", summary)
    if findings:
        raise AgentError(
            "E_LEAKAGE",
            "ID 성격의 열이 feature 에 포함되어 있습니다: " + ", ".join(f.column for f in findings),
            details={"summary": summary},
        )
    return summary


def run_calculate_mass_cost(args: CalculateMassCostArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "calculate_mass_cost"
    eng = _lazy("corp_dl_agent.engineering", feature)
    snap_path = ctx.resolve_input(args.snapshot_path)
    out_dir = ctx.resolve_output_dir(args.output_dir)
    snapshot = _attr(eng, "import_snapshot", feature)(
        snap_path, field_mapping=ctx.cfg.cad.field_mapping or None
    )
    scope_cls = _attr(eng, "MassScope", feature)
    item_cls = _attr(eng, "ScopeItem", feature)

    def items(lst: list[ScopeItemArgs]) -> list[Any]:
        return [item_cls(**i.model_dump()) for i in lst]

    scope = scope_cls(
        include_paint=args.include_paint,
        include_foam=args.include_foam,
        include_adhesive=args.include_adhesive,
        include_purchased=args.include_purchased,
        paint_items=items(args.paint_items),
        foam_items=items(args.foam_items),
        adhesive_items=items(args.adhesive_items),
        purchased_items=items(args.purchased_items),
    )
    material_table = args.material_table or ctx.cfg.cad.material_table
    material_table_path = str(ctx.resolve_input(material_table)) if material_table else None
    table: dict[str, float] | None = (
        _attr(eng, "load_material_table", feature)(material_table_path) if material_table_path else None
    )
    policy = args.density_policy or ctx.cfg.cad.default_density_policy
    mass = _attr(eng, "compute_mass", feature)(
        snapshot,
        scope=scope,
        density_policy=policy,
        material_table=table,
        material_table_path=material_table_path,
    )
    _attr(eng, "write_snapshot", feature)(snapshot, out_dir / "cad_snapshot.json")
    atomic_write_json(out_dir / "mass_result.json", _dump(mass))
    result: dict[str, Any] = {
        "tool": feature,
        "snapshot": str(snap_path),
        "n_rows": len(snapshot.rows),
        "mass_total": _dump(mass.total),
        "mass_complete": bool(mass.complete),
        "mass_missing": list(mass.missing),
        "synthetic": bool(getattr(mass, "synthetic", False)),
        "outputs": {
            "cad_snapshot": str(out_dir / "cad_snapshot.json"),
            "mass_result": str(out_dir / "mass_result.json"),
        },
        "cost": None,
    }
    if args.recipes_path or args.recipe_name:
        if not (args.recipes_path and args.recipe_name):
            raise AgentError(
                "E_INPUT_INVALID", "원가 계산에는 recipes_path 와 recipe_name 이 모두 필요합니다"
            )
        recipes = _attr(eng, "load_recipes", feature)(ctx.resolve_input(args.recipes_path))
        if args.recipe_name not in recipes:
            raise AgentError(
                "E_INPUT_INVALID",
                f"recipe '{args.recipe_name}' 이(가) 없습니다",
                details={"available": sorted(recipes)},
            )
        cost = _attr(eng, "compute_cost", feature)(
            mass,
            recipes[args.recipe_name],
            expected_currency=args.expected_currency,
            expected_base_date=args.expected_base_date,
        )
        atomic_write_json(out_dir / "cost_breakdown.json", _dump(cost))
        result["cost"] = {
            "unit_cost": _dump(cost.unit_cost),
            "currency": cost.currency,
            "base_date": cost.base_date,
            "estimate_type": cost.estimate_type,
            "missing": list(cost.missing),
        }
        result["outputs"]["cost_breakdown"] = str(out_dir / "cost_breakdown.json")
    return result


def _train_budget_override(args: TrainModelArgs, ctx: ToolContext, spec: Any) -> Any:
    """도구 인자의 mode/상한을 ResourceBudget 으로 변환한다.

    - mode 가 TaskSpec.resource_budget.mode 와 같고 별도 상한이 없으면 None (runner 가 TaskSpec 의 예산을 그대로 적용).
    - 그 외에는 cfg.ml.<mode> 예산에서 시작해 인자로 준 상한만 덮어쓴 ResourceBudget 을 만든다 (값을 만들어내지 않는다).
    """
    updates = {
        k: v
        for k, v in (
            ("max_candidates", args.max_candidates),
            ("max_epochs", args.max_epochs),
            ("patience", args.patience),
            ("wall_time_seconds", args.wall_time_seconds),
        )
        if v is not None
    }
    spec_mode = getattr(getattr(spec, "resource_budget", None), "mode", None)
    if not updates and spec_mode == args.mode:
        return None
    budget_mod = _lazy("corp_dl_agent.state.budget", "train_model")
    budget_cls = _attr(budget_mod, "ResourceBudget", "train_model")
    base = budget_cls.from_app_config(ctx.cfg, args.mode)
    return base.model_copy(update=updates) if updates else base


def run_train_model(args: TrainModelArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "train_model"
    runner = _lazy("corp_dl_agent.ml.runner", feature)
    taskspec_mod = _lazy("corp_dl_agent.ml.taskspec", feature)
    ts_path = ctx.resolve_input(args.taskspec_path)
    spec = _attr(taskspec_mod, "load_taskspec", feature)(ts_path)
    run_task = _attr(runner, "run_task", feature)
    if ctx.cfg.ml.device != args.device:
        raise AgentError(
            "E_NOT_SUPPORTED",
            f"train_model 도구는 device={args.device} 만 지원합니다 (설정 ml.device={ctx.cfg.ml.device}). cuda 는 CLI 에서 설정·드라이버 확인 후 사용하세요.",
            details={"tool": feature, "device": args.device, "config_device": ctx.cfg.ml.device},
        )
    summary = run_task(
        spec,
        ctx.cfg,
        ctx.workspace,
        run_id=args.run_id,
        resume=args.resume,
        budget_override=_train_budget_override(args, ctx, spec),
        lock_hash=args.lock_hash,
    )
    run_dir = Path(str(getattr(summary, "run_dir", "")))
    export_dir = run_dir / "export"
    return {
        "tool": feature,
        "task_id": spec.task_id,
        "task_type": spec.task_type,
        "mode": args.mode,
        "run_id": getattr(summary, "run_id", None),
        "status": getattr(summary, "status", None),
        "synthetic": bool(getattr(summary, "synthetic", spec.data_origin == "synthetic")),
        "data_origin": spec.data_origin,
        "selected": getattr(summary, "selected", None),
        "selected_kind": getattr(summary, "selected_kind", None),
        "final_evaluation": dict(getattr(summary, "final_evaluation", {}) or {}),
        "acceptance": dict(getattr(summary, "acceptance", {}) or {}),
        "run_dir": str(run_dir),
        "export_dir": str(export_dir) if export_dir.is_dir() else None,
        "summary": _dump(summary),
    }


def run_predict(args: PredictArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "predict"
    predict_mod = _lazy("corp_dl_agent.ml.predict", feature)
    model_dir = ctx.resolve_input(args.model_dir)
    input_csv = ctx.resolve_input(args.input_csv)
    output_csv = ctx.resolve_output_file(args.output_csv)
    result = _attr(predict_mod, "predict_cli", feature)(
        model_dir,
        input_csv,
        output_csv,
        ctx.cfg,
        allow_synthetic=True if args.allow_synthetic else None,
        extra_roots=[*ctx.input_roots, *ctx.output_roots],
    )
    result_d = _dump(result)
    return {
        "tool": feature,
        "output_csv": str(output_csv),
        "n_rows": result_d.get("n_rows") if isinstance(result_d, dict) else None,
        "synthetic": bool(result_d.get("synthetic", False)) if isinstance(result_d, dict) else None,
        "model_kind": result_d.get("model_kind") if isinstance(result_d, dict) else None,
        "result": result_d,
    }


def run_search_documents(args: SearchDocumentsArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "search_documents"
    index_mod = _lazy("corp_dl_agent.documents.index", feature)
    search_mod = _lazy("corp_dl_agent.documents.search", feature)
    db_path = ctx.resolve_output_file(args.index_db) if args.index_db else ctx.workspace.index_db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    index_cls = _attr(index_mod, "DocumentIndex", feature)
    search_fn = _attr(search_mod, "search", feature)
    include_hidden = bool(args.include_hidden and ctx.cfg.documents.allow_hidden_content_to_llm)
    with index_cls(db_path) as index:
        index_report: Any = None
        if args.roots:
            roots = [ctx.resolve_input(r) for r in args.roots]
            index_report = index.index_roots(
                roots,
                include_hidden_flag=True,
                include_notes=ctx.cfg.documents.include_notes,
                max_file_mb=ctx.cfg.documents.max_index_file_mb,
                forbid_links=ctx.cfg.security.forbid_symlinks,
            )
        hits = search_fn(index, args.query, limit=args.limit, include_hidden=include_hidden, kind=args.kind)
    return {
        "tool": feature,
        "query": args.query,
        "index_db": str(db_path),
        "include_hidden": include_hidden,
        "hidden_requested_but_denied": bool(args.include_hidden and not include_hidden),
        "n_hits": len(hits),
        "hits": [_dump(h) for h in hits],
        "index_report": _dump(index_report) if index_report is not None else None,
    }


def run_generate_report(args: GenerateReportArgs, ctx: ToolContext) -> dict[str, Any]:
    feature = "generate_report"
    payload_mod = _lazy("corp_dl_agent.documents.payload", feature)
    templates_mod = _lazy("corp_dl_agent.documents.templates", feature)
    validation_mod = _lazy("corp_dl_agent.documents.validation", feature)
    if not (args.pptx_template or args.xlsx_template):
        raise AgentError("E_INPUT_INVALID", "pptx_template 또는 xlsx_template 중 하나 이상이 필요합니다")
    payload_path = ctx.resolve_input(args.payload_path)
    out_dir = ctx.resolve_output_dir(args.output_dir)
    payload = _attr(payload_mod, "load_payload", feature)(payload_path)
    payload_validation = _attr(payload_mod, "validate_payload", feature)(payload)
    manifests: list[Any] = []
    pptx_out: Path | None = None
    xlsx_out: Path | None = None
    if args.pptx_template:
        pptx_out = out_dir / "review.pptx"
        manifests.append(
            _attr(templates_mod, "fill_pptx", feature)(
                ctx.resolve_input(args.pptx_template),
                payload,
                pptx_out,
                include_notes=ctx.cfg.documents.include_notes,
            )
        )
    if args.xlsx_template:
        xlsx_out = out_dir / "comparison.xlsx"
        manifests.append(
            _attr(templates_mod, "fill_xlsx", feature)(
                ctx.resolve_input(args.xlsx_template), payload, xlsx_out
            )
        )
    report = _attr(validation_mod, "validate_documents", feature)(
        pptx_out,
        xlsx_out,
        payload,
        manifests=manifests,
        payload_validation=payload_validation,
        renderer=ctx.cfg.documents.renderer,
        recalc_engine=ctx.cfg.documents.recalc_engine,
    )
    atomic_write_json(out_dir / "document_manifest.json", [_dump(m) for m in manifests])
    atomic_write_json(out_dir / "validation_report.json", _dump(report))
    return {
        "tool": feature,
        "payload": str(payload_path),
        "synthetic": bool(getattr(payload, "synthetic", False)),
        "payload_validation": _dump(payload_validation),
        "validation_status": str(getattr(report, "status", "")),
        "outputs": {
            "review_pptx": str(pptx_out) if pptx_out else None,
            "comparison_xlsx": str(xlsx_out) if xlsx_out else None,
            "document_manifest": str(out_dir / "document_manifest.json"),
            "validation_report": str(out_dir / "validation_report.json"),
        },
    }


# --------------------------------------------------------------------------- registry / dispatch

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "validate_input": ToolSpec(
        name="validate_input",
        input_model=ValidateInputArgs,
        description_ko="TaskSpec 데이터 계약·인코딩·고정 그룹 분할·누수 검사 (학습 없음)",
        side_effects=("reads_files", "writes_files"),
        requires=("corp_dl_agent.ml.data", "corp_dl_agent.ml.split", "corp_dl_agent.ml.preprocessing"),
    ),
    "calculate_mass_cost": ToolSpec(
        name="calculate_mass_cost",
        input_model=CalculateMassCostArgs,
        description_ko="CAD snapshot(CSV/JSON) 수입 후 중량 계산, recipe 가 있으면 원가 산출",
        side_effects=("reads_files", "writes_files"),
        requires=("corp_dl_agent.engineering",),
    ),
    "train_model": ToolSpec(
        name="train_model",
        input_model=TrainModelArgs,
        description_ko="기준 모델 + MLP 후보 학습·validation 선택·test 1회 평가 (CPU)",
        side_effects=("reads_files", "writes_files", "trains_model"),
        requires=("corp_dl_agent.ml.runner",),
    ),
    "predict": ToolSpec(
        name="predict",
        input_model=PredictArgs,
        description_ko="export 된 모델 bundle 로 CSV 예측 (신뢰 checksum 일치 bundle 만)",
        side_effects=("reads_files", "writes_files"),
        requires=("corp_dl_agent.ml.predict",),
    ),
    "search_documents": ToolSpec(
        name="search_documents",
        input_model=SearchDocumentsArgs,
        description_ko="로컬 PPTX/XLSX 색인에서 키워드 근거 검색 (hidden 기본 제외)",
        side_effects=("reads_files", "indexes_documents"),
        requires=("corp_dl_agent.documents.index", "corp_dl_agent.documents.search"),
    ),
    "generate_report": ToolSpec(
        name="generate_report",
        input_model=GenerateReportArgs,
        description_ko="report_payload 로 승인 템플릿의 placeholder/named range 만 채워 PPTX/XLSX 생성·검증",
        side_effects=("reads_files", "writes_files"),
        requires=("corp_dl_agent.documents.templates", "corp_dl_agent.documents.validation"),
    ),
}

_IMPLEMENTATIONS: dict[str, Callable[[Any, ToolContext], dict[str, Any]]] = {
    "validate_input": run_validate_input,
    "calculate_mass_cost": run_calculate_mass_cost,
    "train_model": run_train_model,
    "predict": run_predict,
    "search_documents": run_search_documents,
    "generate_report": run_generate_report,
}


def tool_names() -> list[str]:
    return list(TOOL_REGISTRY)


def describe_tools() -> list[dict[str, Any]]:
    return [spec.describe() for spec in TOOL_REGISTRY.values()]


def get_tool(name: str) -> ToolSpec:
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        raise AgentError(
            "E_TOOL_NOT_REGISTERED",
            f"등록되지 않은 도구입니다: '{name}'",
            details={"registered": tool_names()},
        )
    return spec


def _format_validation_error(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        out.append(f"{loc}: {e.get('msg', '')}")
    return out


def validate_arguments(name: str, payload: Any, *, error_code: str = "E_SCHEMA_INVALID") -> StrictModel:
    """도구 이름과 인자를 strict 검증한다. 미등록 도구 -> E_TOOL_NOT_REGISTERED, 인자 오류 -> error_code."""
    spec = get_tool(name)
    if not isinstance(payload, dict):
        raise AgentError(error_code, f"도구 '{name}' 의 인자는 객체(dict) 이어야 합니다")
    try:
        model = validate_strict(spec.input_model, payload)
    except ValidationError as exc:
        raise AgentError(
            error_code,
            f"도구 '{name}' 의 인자가 허용 범위를 벗어났습니다",
            details={"tool": name, "errors": _format_validation_error(exc)},
        ) from exc
    assert isinstance(model, StrictModel)
    return model


def dispatch(name: str, payload: Any, ctx: ToolContext) -> dict[str, Any]:
    """등록 도구를 실행한다. 이름/인자 검증 -> 경로 승인 검사 -> 코어 함수 호출(lazy import).

    LLM 출력은 여기서 데이터로만 다뤄진다. eval/exec/subprocess 는 사용하지 않는다.
    """
    args = validate_arguments(name, payload)
    impl = _IMPLEMENTATIONS[name]
    return impl(args, ctx)


__all__ = [
    "TOOL_REGISTRY",
    "CalculateMassCostArgs",
    "GenerateReportArgs",
    "PredictArgs",
    "ScopeItemArgs",
    "SearchDocumentsArgs",
    "ToolContext",
    "ToolSpec",
    "TrainModelArgs",
    "ValidateInputArgs",
    "describe_tools",
    "dispatch",
    "get_tool",
    "tool_names",
    "validate_arguments",
]
