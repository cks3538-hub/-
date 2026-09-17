"""adapters.tools 시험: 등록 도구 6종·strict 인자·dispatch 가 실제 코어 함수(engineering)로 계산, 미등록/의존성 부재 처리."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.adapters import tools as tools_mod
from corp_dl_agent.adapters.tools import (
    TOOL_REGISTRY,
    ToolContext,
    describe_tools,
    dispatch,
    validate_arguments,
)
from corp_dl_agent.common import read_json
from corp_dl_agent.config import load_config
from corp_dl_agent.errors import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPECTED_TOOLS = {
    "validate_input",
    "calculate_mass_cost",
    "train_model",
    "predict",
    "search_documents",
    "generate_report",
}


def make_ctx(tmp_path: Path) -> ToolContext:
    cfg = load_config(
        overrides={
            "paths.data_root": str(tmp_path / "ws"),
            "paths.input_roots": f"[{FIXTURES}]",
            "paths.output_root": str(tmp_path / "out"),
        }
    )
    return ToolContext.from_config(cfg)


def test_registry_has_exactly_six_tools_with_strict_models() -> None:
    assert set(TOOL_REGISTRY) == EXPECTED_TOOLS
    described = describe_tools()
    assert {d["name"] for d in described} == EXPECTED_TOOLS
    for d in described:
        assert d["description_ko"] and d["input_schema"]["additionalProperties"] is False
        assert d["side_effects"] and d["requires"]
    assert set(tools_mod._IMPLEMENTATIONS) == EXPECTED_TOOLS


def test_unregistered_tool_rejected(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    with pytest.raises(AgentError) as ei:
        dispatch("run_shell", {"cmd": "rm -rf /"}, ctx)
    assert ei.value.code == "E_TOOL_NOT_REGISTERED" and sorted(ei.value.details["registered"]) == sorted(
        EXPECTED_TOOLS
    )
    with pytest.raises(AgentError) as ei2:
        validate_arguments("eval", {})
    assert ei2.value.code == "E_TOOL_NOT_REGISTERED"


def test_strict_arguments_reject_extra_fields_types_and_ranges() -> None:
    with pytest.raises(AgentError) as ei:
        validate_arguments("search_documents", {"query": "x", "shell": "id"})
    assert ei.value.code == "E_SCHEMA_INVALID" and any("shell" in e for e in ei.value.details["errors"])
    with pytest.raises(AgentError) as ei2:
        validate_arguments("search_documents", {"query": "x", "limit": "10"})  # "10" -> int 거부 (strict)
    assert ei2.value.code == "E_SCHEMA_INVALID"
    with pytest.raises(AgentError) as ei3:
        validate_arguments("search_documents", {"query": "x", "limit": 101})
    assert ei3.value.code == "E_SCHEMA_INVALID"
    with pytest.raises(AgentError) as ei4:
        validate_arguments("calculate_mass_cost", ["not", "a", "dict"])
    assert ei4.value.code == "E_SCHEMA_INVALID"
    with pytest.raises(AgentError) as ei5:
        validate_arguments("train_model", {"taskspec_path": "t.yaml", "device": "cuda"})
    assert ei5.value.code == "E_SCHEMA_INVALID"
    model = validate_arguments(
        "calculate_mass_cost", {"snapshot_path": "a.csv", "output_dir": "o", "include_paint": True}
    )
    assert isinstance(model, tools_mod.CalculateMassCostArgs) and model.include_paint is True
    custom = validate_arguments(
        "predict",
        {"model_dir": "m", "input_csv": "i.csv", "output_csv": "o.csv"},
        error_code="E_LLM_PLAN_INVALID",
    )
    assert isinstance(custom, tools_mod.PredictArgs)


def test_dispatch_calculate_mass_cost_uses_real_engineering(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    out_dir = tmp_path / "out" / "A"
    result = dispatch(
        "calculate_mass_cost",
        {"snapshot_path": str(FIXTURES / "cad" / "asm_a_snapshot.csv"), "output_dir": str(out_dir)},
        ctx,
    )
    assert result["tool"] == "calculate_mass_cost" and result["n_rows"] == 9 and result["synthetic"] is True
    assert result["mass_total"]["value"] == pytest.approx(4.675) and result["mass_total"]["unit"] == "kg"
    assert result["mass_total"]["value_type"] == "calculated" and result["mass_complete"] is False
    assert len(result["mass_missing"]) == 3 and result["cost"] is None
    mass = read_json(out_dir / "mass_result.json")
    cube = next(i for i in mass["items"] if i["occurrence_path"] == "ROOT/ASM_A/PART_CUBE.1")
    assert (
        cube["unit_mass"]["value"] == pytest.approx(1.0) and cube["status"] == "ok"
    )  # 1,000,000 mm3 x 1.0 g/cm3
    assert cube["unit_mass"]["calculation_id"] and cube["unit_mass"]["value_type"] == "calculated"
    assert (out_dir / "cad_snapshot.json").is_file()
    assert read_json(out_dir / "cad_snapshot.json")["synthetic"] is True


def test_dispatch_calculate_mass_cost_with_recipe_and_material_table(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    out_dir = tmp_path / "out" / "B"
    result = dispatch(
        "calculate_mass_cost",
        {
            "snapshot_path": str(FIXTURES / "cad" / "asm_a_snapshot.csv"),
            "output_dir": str(out_dir),
            "recipes_path": str(FIXTURES / "cad" / "cost_recipes.json"),
            "recipe_name": "injection_synthetic_pp",
            "material_table": str(FIXTURES / "cad" / "material_table.csv"),
            "density_policy": "use_material_table",
            "expected_currency": "KRW",
            "expected_base_date": "2026-09-01",
        },
        ctx,
    )
    assert result["cost"] is not None and result["cost"]["currency"] == "KRW"
    assert (
        result["cost"]["base_date"] == "2026-09-01"
        and result["cost"]["estimate_type"] == "manufacturing_estimate"
    )
    assert (out_dir / "cost_breakdown.json").is_file()
    # 재료표 정책: 밀도 누락 부품(PP-GF30) 이 재료표 값으로 assumed 계산되어 누락 목록에서 빠진다
    mass = read_json(out_dir / "mass_result.json")
    nod = next(i for i in mass["items"] if i["occurrence_path"] == "ROOT/ASM_A/PART_NODENSITY.1")
    assert nod["status"] == "ok" and nod["unit_mass"]["assumption"] is True
    assert result["mass_total"]["value"] > 4.675
    with pytest.raises(AgentError) as ei:
        dispatch(
            "calculate_mass_cost",
            {
                "snapshot_path": str(FIXTURES / "cad" / "asm_a_snapshot.csv"),
                "output_dir": str(out_dir),
                "recipe_name": "x",
            },
            ctx,
        )
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        dispatch(
            "calculate_mass_cost",
            {
                "snapshot_path": str(FIXTURES / "cad" / "asm_a_snapshot.csv"),
                "output_dir": str(out_dir),
                "recipes_path": str(FIXTURES / "cad" / "cost_recipes.json"),
                "recipe_name": "injection_synthetic_pp",
                "expected_currency": "USD",
            },
            ctx,
        )
    assert ei2.value.code == "E_REVISION_MISMATCH"


def test_dispatch_rejects_paths_outside_approved_roots(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    outside = Path("/") / "definitely-not-approved" / "snap.csv"
    with pytest.raises(AgentError) as ei:
        dispatch(
            "calculate_mass_cost",
            {"snapshot_path": str(outside), "output_dir": str(tmp_path / "out" / "x")},
            ctx,
        )
    assert ei.value.code == "E_PATH_OUTSIDE_ROOT"
    with pytest.raises(AgentError) as ei2:
        dispatch(
            "calculate_mass_cost",
            {
                "snapshot_path": str(FIXTURES / "cad" / "asm_a_snapshot.csv"),
                "output_dir": "/definitely-not-approved/out",
            },
            ctx,
        )
    assert ei2.value.code == "E_PATH_OUTSIDE_ROOT"
    assert not Path("/definitely-not-approved").exists()  # 검사 전에 폴더를 만들지 않는다


@pytest.mark.parametrize(
    ("tool", "module", "payload"),
    [
        ("train_model", "corp_dl_agent.ml.runner", {"taskspec_path": "fixtures/ml/task_regression.yaml"}),
        (
            "predict",
            "corp_dl_agent.ml.predict",
            {
                "model_dir": "fixtures",
                "input_csv": "fixtures/ml/clip_regression.csv",
                "output_csv": "out.csv",
            },
        ),
    ],
)
def test_missing_implementation_module_is_blocked_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, module: str, payload: dict[str, Any]
) -> None:
    monkeypatch.setitem(sys.modules, module, None)  # import 시 ImportError 유도 (ml-torch 완료 여부와 무관)
    ctx = make_ctx(tmp_path)
    args = {
        k: (str(FIXTURES.parent / v) if isinstance(v, str) and v.startswith("fixtures") else v)
        for k, v in payload.items()
    }
    if "output_csv" in args:
        args["output_csv"] = str(tmp_path / "out" / "pred.csv")
    with pytest.raises(AgentError) as ei:
        dispatch(tool, args, ctx)
    assert ei.value.code == "E_BLOCKED_DEPENDENCY" and ei.value.details["package"] == module


def test_missing_attribute_is_blocked_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.setitem(sys.modules, "corp_dl_agent.ml.runner", types.ModuleType("corp_dl_agent.ml.runner"))
    ctx = make_ctx(tmp_path)
    with pytest.raises(AgentError) as ei:
        dispatch("train_model", {"taskspec_path": str(FIXTURES / "ml" / "task_regression.yaml")}, ctx)
    assert ei.value.code == "E_BLOCKED_DEPENDENCY" and ei.value.details["attribute"] == "run_task"


def test_validate_input_runs_leakage_checks_without_training(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    ctx = make_ctx(tmp_path)
    result = dispatch(
        "validate_input",
        {
            "taskspec_path": str(FIXTURES / "ml" / "task_regression.yaml"),
            "output_dir": str(tmp_path / "out" / "validate"),
        },
        ctx,
    )
    assert (
        result["training_performed"] is False
        and result["status"] == "PASS"
        and result["overlap_checked"] is True
    )
    assert result["synthetic"] is True and result["n_rows"] > 0
    assert (
        Path(result["outputs"]["split_manifest"]).is_file()
        and Path(result["outputs"]["data_report"]).is_file()
    )


@pytest.mark.documents
def test_search_documents_and_generate_report_via_dispatch(tmp_path: Path) -> None:
    pytest.importorskip("pptx")
    pytest.importorskip("openpyxl")
    ctx = make_ctx(tmp_path)
    docs = FIXTURES / "documents"
    result = dispatch(
        "search_documents",
        {"query": "원가", "roots": [str(docs)], "limit": 5, "index_db": str(tmp_path / "out" / "idx.sqlite")},
        ctx,
    )
    assert result["tool"] == "search_documents" and result["index_report"] is not None
    assert result["n_hits"] >= 1 and all(h["hidden"] is False for h in result["hits"])
    assert result["include_hidden"] is False
    denied = dispatch(
        "search_documents",
        {"query": "원가", "include_hidden": True, "index_db": str(tmp_path / "out" / "idx.sqlite")},
        ctx,
    )
    assert denied["hidden_requested_but_denied"] is True  # documents.allow_hidden_content_to_llm=false
    report = dispatch(
        "generate_report",
        {
            "payload_path": str(docs / "report_payload.example.json"),
            "output_dir": str(tmp_path / "out" / "report"),
            "pptx_template": str(docs / "template_review.pptx"),
            "xlsx_template": str(docs / "template_comparison.xlsx"),
        },
        ctx,
    )
    assert report["tool"] == "generate_report" and report["synthetic"] is True
    assert (
        Path(report["outputs"]["review_pptx"]).is_file()
        and Path(report["outputs"]["comparison_xlsx"]).is_file()
    )
    assert Path(report["outputs"]["validation_report"]).is_file()
    with pytest.raises(AgentError) as ei:
        dispatch(
            "generate_report",
            {
                "payload_path": str(docs / "report_payload.example.json"),
                "output_dir": str(tmp_path / "out" / "r2"),
            },
            ctx,
        )
    assert ei.value.code == "E_INPUT_INVALID"


def test_tool_context_roots_from_config(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    assert FIXTURES.resolve() in ctx.input_roots and ctx.workspace.outputs in ctx.output_roots
    assert (tmp_path / "out").resolve() in ctx.output_roots
    ctx2 = ToolContext.from_config(ctx.cfg, extra_roots=[tmp_path / "extra"])
    assert (tmp_path / "extra").resolve() in ctx2.input_roots
