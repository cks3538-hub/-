"""원가 계산 시험: 사출가공비 공식, MISSING 처리, 중복 반영 거부, 통화/기준일 불일치, 견적/제조원가 구분."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corp_dl_agent.common import Quantity
from corp_dl_agent.engineering.cad_snapshot import import_snapshot
from corp_dl_agent.engineering.cost import (
    CostBreakdown,
    GenericProcessRecipe,
    InjectionMoldingRecipe,
    PurchasedPartRecipe,
    compute_cost,
    load_recipes,
    parse_recipe,
)
from corp_dl_agent.engineering.mass import MassItem, MassResult, compute_mass
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"
PROCESS_COST = 36000 * 30 / (3600 * 2 * 0.95)  # 157.894736...


@pytest.fixture(scope="module")
def mass_a() -> MassResult:
    return compute_mass(import_snapshot(FIXTURES / "asm_a_snapshot.csv"))


@pytest.fixture(scope="module")
def cube(mass_a: MassResult) -> MassItem:
    return mass_a.item("ROOT/ASM_A/PART_CUBE.1")


@pytest.fixture(scope="module")
def recipes() -> dict:
    return load_recipes(FIXTURES / "cost_recipes.json")


def _base(**kw: object) -> dict[str, object]:
    d: dict[str, object] = {
        "recipe_type": "injection_molding",
        "currency": "KRW",
        "base_date": "2026-09-01",
        "material_price_per_kg": 2500,
        "cycle_time_s": 30,
        "cavities": 2,
        "good_rate": 0.95,
        "machine_rate_per_hour": 36000,
        "runner_ratio": 0.1,
        "post_process_cost": 50,
        "assembly_cost": 30,
        "tooling_cost": 50_000_000,
        "tooling_amortization_qty": 100_000,
        "production_volume": 100_000,
        "setup_cost_per_batch": 200_000,
        "batch_size": 5000,
    }
    d.update(kw)
    return d


def test_injection_process_formula(cube: MassItem, recipes: dict) -> None:
    cb = compute_cost(cube, recipes["injection_synthetic_pp"])
    proc = cb.line("사출가공비")
    assert proc.amount.value == pytest.approx(PROCESS_COST, rel=1e-12)
    assert proc.amount.value == pytest.approx(157.89473684210526, rel=1e-12)
    assert proc.category == "process"
    assert proc.formula == "36,000 KRW/h × 30 s / (3600 × 2 × 0.95)"
    assert proc.amount.unit == "KRW/unit"
    assert proc.amount.value_type == "calculated"
    assert proc.amount.calculation_version == CALCULATION_VERSION


def test_injection_full_breakdown(cube: MassItem, recipes: dict) -> None:
    cb = compute_cost(cube, recipes["injection_synthetic_pp"])
    assert isinstance(cb, CostBreakdown)
    assert [ln.name for ln in cb.lines] == [
        "재료비",
        "사출가공비",
        "후가공비",
        "조립비",
        "구매품",
        "금형상각",
        "setup",
        "포장",
    ]
    assert cb.line("재료비").amount.value == pytest.approx(2750.0)  # 2500 × 1.0 kg × (1 + 0.1)
    assert cb.line("후가공비").amount.value == 50.0
    assert cb.line("조립비").amount.value == 30.0
    assert cb.line("구매품").amount.value == 240.0
    assert cb.line("금형상각").amount.value == 500.0
    assert cb.line("setup").amount.value == 40.0
    assert cb.line("포장").amount.value == 15.0
    assert cb.unit_cost.value == pytest.approx(2750 + PROCESS_COST + 50 + 30 + 240 + 500 + 40 + 15)
    assert cb.complete is True and cb.missing == []
    assert cb.currency == "KRW" and cb.base_date == "2026-09-01"
    assert cb.quantity_basis == 100_000
    assert cb.estimate_type == "manufacturing_estimate"
    assert cb.unit_cost.calculation_version == CALCULATION_VERSION and cb.unit_cost.assumption is False
    assert cb.mass_basis.value == 1.0 and cb.subject == "ROOT/ASM_A/PART_CUBE.1"
    assert cb.synthetic is True
    assert any("regrind 미허용" in a for a in cb.assumptions)
    assert any("양품률 미반영" in a for a in cb.assumptions)


def test_runner_regrind_not_double_charged(cube: MassItem, recipes: dict) -> None:
    cb = compute_cost(cube, recipes["injection_regrind"])
    assert cb.line("재료비").amount.value == pytest.approx(2500.0)  # runner 재료 미과금
    assert any("runner 재료 미과금" in a for a in cb.assumptions)
    # runner 를 other 항목으로 또 넣으면 recipe 검증에서 거부
    with pytest.raises(AgentError) as ei:
        parse_recipe(_base(other=[{"name": "런너 손실", "value": 100}]))
    assert ei.value.code == "E_SCHEMA_INVALID"
    assert any("runner" in e for e in ei.value.details["errors"])


def test_setup_yield_tooling_double_counting_rejected() -> None:
    for other, word in (
        ([{"name": "setup 비용", "value": 10}], "setup"),
        ([{"name": "불량 손실", "value": 10}], "양품률"),
        ([{"name": "금형 유지비", "value": 10}], "금형"),
        ([{"name": "포장", "value": 1}, {"name": "포장", "value": 2}], "중복"),
    ):
        with pytest.raises(AgentError) as ei:
            parse_recipe(_base(other=other))
        assert ei.value.code == "E_SCHEMA_INVALID"
        assert any(word in e for e in ei.value.details["errors"])
    with pytest.raises(AgentError):
        parse_recipe(_base(runner_ratio=0.0, regrind_allowed=True))


def test_good_rate_applied_once_to_material_when_flagged(cube: MassItem) -> None:
    r = parse_recipe(_base(apply_good_rate_to_material=True))
    cb = compute_cost(cube, r)
    assert cb.line("재료비").amount.value == pytest.approx(2750.0 / 0.95)
    assert cb.line("사출가공비").amount.value == pytest.approx(PROCESS_COST)
    assert any("양품률 0.95 1회 반영" in a for a in cb.assumptions)


def test_missing_price_and_volume_give_missing(cube: MassItem, recipes: dict) -> None:
    cb = compute_cost(cube, recipes["injection_missing_price"])
    assert cb.complete is False
    assert cb.unit_cost.is_missing() and cb.unit_cost.value_type == "missing"
    assert cb.line("재료비").amount.is_missing()
    assert cb.line("금형상각").amount.is_missing()
    assert cb.line("사출가공비").amount.value == pytest.approx(PROCESS_COST)  # 계산 가능한 line 은 계산
    assert any("material_price_per_kg" in m for m in cb.missing)
    assert any("생산량" in m or "production_volume" in m for m in cb.missing)
    assert cb.quantity_basis == 1
    assert any("production_volume" in a for a in cb.assumptions)


def test_missing_mass_basis_gives_missing_material(recipes: dict) -> None:
    cb = compute_cost(Quantity.missing("kg", "unloaded"), recipes["injection_synthetic_pp"])
    assert cb.line("재료비").amount.is_missing()
    assert cb.unit_cost.is_missing() and cb.complete is False
    assert cb.warnings


def test_purchased_quote_vs_manufacturing(cube: MassItem, recipes: dict) -> None:
    q = compute_cost(cube, recipes["purchased_clip_quote"])
    assert q.estimate_type == "quote"
    assert q.unit_cost.value == 120.0 and q.quantity_basis == 1000
    assert q.recipe_type == "purchased_part"
    m = compute_cost(cube, recipes["purchased_missing_price"])
    assert m.unit_cost.is_missing() and m.complete is False
    assert isinstance(recipes["purchased_clip_quote"], PurchasedPartRecipe)


def test_generic_recipe(mass_a: MassResult, recipes: dict) -> None:
    br = mass_a.item("ROOT/ASM_A/PART_BRACKET.1")
    cb = compute_cost(br, recipes["generic_stamping"])
    assert isinstance(recipes["generic_stamping"], GenericProcessRecipe)
    assert cb.line("재료비").amount.value == pytest.approx(3200 * 0.675)
    assert cb.unit_cost.value == pytest.approx(3200 * 0.675 + 85 + 40)
    assert cb.recipe_type == "generic" and cb.complete is True


def test_generic_unit_errors(cube: MassItem) -> None:
    bad = parse_recipe(
        {
            "recipe_type": "generic",
            "currency": "KRW",
            "base_date": "2026-09-01",
            "lines": [{"name": "x", "value": 1, "unit": "KRW/each"}],
        }
    )
    with pytest.raises(AgentError) as ei:
        compute_cost(cube, bad)
    assert ei.value.code == "E_UNIT_MISMATCH"
    other_cur = parse_recipe(
        {
            "recipe_type": "generic",
            "currency": "KRW",
            "base_date": "2026-09-01",
            "lines": [{"name": "x", "value": 1, "unit": "USD/unit"}],
        }
    )
    with pytest.raises(AgentError) as ei2:
        compute_cost(cube, other_cur)
    assert ei2.value.code == "E_REVISION_MISMATCH"


def test_currency_and_base_date_mismatch(cube: MassItem, recipes: dict) -> None:
    with pytest.raises(AgentError) as ei:
        compute_cost(cube, recipes["injection_usd_2025"], expected_currency="KRW")
    assert ei.value.code == "E_REVISION_MISMATCH"
    with pytest.raises(AgentError) as ei2:
        compute_cost(cube, recipes["injection_synthetic_pp"], expected_base_date="2026-01-01")
    assert ei2.value.code == "E_REVISION_MISMATCH"
    # 구매품 통화가 recipe 와 다름
    r = parse_recipe(
        _base(purchased_items=[{"name": "clip", "unit_price": 0.1, "quantity": 2, "currency": "USD"}])
    )
    with pytest.raises(AgentError) as ei3:
        compute_cost(cube, r)
    assert ei3.value.code == "E_REVISION_MISMATCH"
    r2 = parse_recipe(
        _base(purchased_items=[{"name": "clip", "unit_price": 100, "quantity": 2, "base_date": "2025-12-31"}])
    )
    with pytest.raises(AgentError) as ei4:
        compute_cost(cube, r2)
    assert ei4.value.code == "E_REVISION_MISMATCH"
    # 일치하면 통과
    ok = compute_cost(
        cube, recipes["injection_synthetic_pp"], expected_currency="krw", expected_base_date="2026-09-01"
    )
    assert ok.complete


def test_mass_unit_must_be_kg(recipes: dict) -> None:
    with pytest.raises(AgentError) as ei:
        compute_cost(Quantity(value=1000.0, unit="g", value_type="source"), recipes["injection_synthetic_pp"])
    assert ei.value.code == "E_UNIT_MISMATCH"


def test_invalid_recipes_rejected() -> None:
    for bad in (
        _base(material_price_per_kg=-1),
        _base(base_date="2026/09/01"),
        _base(currency="원화"),
        _base(currency="KRWX"),
        _base(good_rate=1.5),
        _base(cavities=0),
        _base(unknown_field=1),
        {"recipe_type": "casting", "currency": "KRW", "base_date": "2026-09-01"},
        {"recipe_type": "generic", "currency": "KRW", "base_date": "2026-09-01", "lines": []},
    ):
        with pytest.raises(AgentError) as ei:
            parse_recipe(bad)
        assert ei.value.code == "E_SCHEMA_INVALID"


def test_load_recipes_marks_synthetic_and_names(recipes: dict) -> None:
    assert set(recipes) >= {
        "injection_synthetic_pp",
        "purchased_clip_quote",
        "generic_stamping",
        "injection_missing_price",
    }
    assert all(r.synthetic for r in recipes.values())
    assert recipes["injection_synthetic_pp"].name == "injection_synthetic_pp"
    assert isinstance(recipes["injection_synthetic_pp"], InjectionMoldingRecipe)


def test_load_recipes_bad_file(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"recipes": []}), encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        load_recipes(p)
    assert ei.value.code == "E_SCHEMA_INVALID"
    with pytest.raises(AgentError):
        load_recipes(tmp_path / "없음.json")


def test_mass_result_as_subject(mass_a: MassResult, recipes: dict) -> None:
    cb = compute_cost(mass_a, recipes["injection_synthetic_pp"])
    assert cb.subject == "assembly_total"
    assert cb.mass_basis.value == pytest.approx(4.675)
    assert cb.line("재료비").amount.value == pytest.approx(2500 * 4.675 * 1.1)


def test_other_per_kg_line(cube: MassItem) -> None:
    r = parse_recipe(_base(other=[{"name": "안료", "value": 100, "unit": "per_kg"}]))
    cb = compute_cost(cube, r)
    assert cb.line("안료").amount.value == pytest.approx(100.0)
    r2 = parse_recipe(_base(other=[{"name": "안료", "value": None}]))
    cb2 = compute_cost(cube, r2)
    assert cb2.line("안료").amount.is_missing() and cb2.complete is False


def test_cost_breakdown_json_roundtrip(cube: MassItem, recipes: dict, tmp_path: Path) -> None:
    from corp_dl_agent.common import atomic_write_json, read_json, validate_strict

    cb = compute_cost(cube, recipes["injection_synthetic_pp"])
    p = tmp_path / "cost_breakdown.json"
    atomic_write_json(p, cb.model_dump(mode="json"))
    loaded = validate_strict(CostBreakdown, read_json(p))
    assert loaded.unit_cost.value == cb.unit_cost.value
