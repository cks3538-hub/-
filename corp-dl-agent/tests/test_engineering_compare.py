"""설계안 비교 시험: 동일 조건 검증, revision/기준일/통화/단위 불일치 거부, 누락값 거부, delta/unknown 출력."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from corp_dl_agent.common import Quantity, atomic_write_json
from corp_dl_agent.engineering.cad_snapshot import import_snapshot, write_snapshot
from corp_dl_agent.engineering.compare import (
    DesignCandidate,
    DesignComparison,
    compare_designs,
    load_candidate_dir,
)
from corp_dl_agent.engineering.cost import compute_cost, load_recipes
from corp_dl_agent.engineering.mass import MassResult, compute_mass
from corp_dl_agent.errors import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"


def _candidate(
    cid: str, name: str, *, revision: str = "R1", with_cost: bool = True, **kw: object
) -> DesignCandidate:
    snap = import_snapshot(FIXTURES / name)
    mass = compute_mass(snap)
    cost = None
    if with_cost:
        recipes = load_recipes(FIXTURES / "cost_recipes.json")
        cube = next(i for i in mass.items if i.reference_id == "PART_CUBE")
        cost = compute_cost(cube, recipes["injection_synthetic_pp"])
    data: dict[str, object] = {
        "id": cid,
        "label": f"설계안 {cid}",
        "snapshot_path": str(FIXTURES / name),
        "revision": revision,
        "base_date": "2026-09-01" if with_cost else None,
        "currency": "KRW" if with_cost else None,
        "mass": mass,
        "cost": cost,
    }
    data.update(kw)
    return DesignCandidate(**data)  # type: ignore[arg-type]


def test_compare_a_b_consistent() -> None:
    a = _candidate(
        "A",
        "asm_a_snapshot.csv",
        constraints={"패키지 간섭 없음": True},
        evidence=["review_2026.pptx#slide:3"],
    )
    b = _candidate(
        "B", "asm_b_snapshot.csv", constraints={"패키지 간섭 없음": None}, unknowns=["도장 사양 미확정"]
    )
    cmp = compare_designs([a, b])
    assert isinstance(cmp, DesignComparison)
    assert cmp.consistent is True and cmp.warnings == []
    assert cmp.revision == "R1" and cmp.base_date == "2026-09-01" and cmp.currency == "KRW"
    assert cmp.baseline_id == "A"
    assert [r.candidate_id for r in cmp.rows] == ["A", "B"]
    ra, rb = cmp.rows
    assert ra.mass_total.value == pytest.approx(4.675) and rb.mass_total.value == pytest.approx(3.5425)
    assert ra.mass_complete is False and len(ra.mass_missing) == 3
    assert ra.unit_cost.value == pytest.approx(3782.8947368421054)
    assert rb.unit_cost.value == pytest.approx(3782.8947368421054 - 550.0)  # 재료비 2500×0.8×1.1 = 2200
    assert ra.estimate_type == "manufacturing_estimate"
    assert ra.constraints_ok is True and rb.constraints_ok is None
    assert ra.evidence == ["review_2026.pptx#slide:3"]
    assert "도장 사양 미확정" in rb.unknowns
    assert any(u.startswith("mass: ") for u in rb.unknowns)
    assert any("constraint: 패키지 간섭 없음 미확인" == u for u in rb.unknowns)
    deltas = {d.metric: d for d in cmp.deltas}
    assert deltas["mass_total"].delta.value == pytest.approx(3.5425 - 4.675)
    assert deltas["mass_total"].delta.unit == "kg" and deltas["mass_total"].delta.value_type == "calculated"
    assert deltas["mass_total"].relative == pytest.approx((3.5425 - 4.675) / 4.675)
    assert deltas["unit_cost"].delta.value == pytest.approx(-550.0)
    assert deltas["unit_cost"].delta.calculation_id == "delta:unit_cost:B-A"
    assert cmp.complete is False  # mass 누락 있음
    assert cmp.synthetic is True


def test_revision_mismatch_between_candidates_rejected() -> None:
    a = _candidate("A", "asm_a_snapshot.csv")
    b_snap = import_snapshot(FIXTURES / "asm_b_snapshot.csv")
    for r in b_snap.rows:
        r.revision = "R2"
    b = DesignCandidate(
        id="B",
        label="B",
        snapshot_path="b",
        revision="R2",
        base_date="2026-09-01",
        currency="KRW",
        mass=compute_mass(b_snap),
    )
    with pytest.raises(AgentError) as ei:
        compare_designs([a, b])
    assert ei.value.code == "E_REVISION_MISMATCH"
    assert "revision" in ei.value.message
    # 정책 완화: 경고로 기록, consistent=False
    cmp = compare_designs([a, b], require_same_revision_policy=False)
    assert cmp.consistent is False and cmp.revision is None
    assert any("revision 불일치" in w for w in cmp.warnings)


def test_declared_revision_must_match_snapshot() -> None:
    a = _candidate("A", "asm_a_snapshot.csv")
    b = _candidate("B", "asm_b_snapshot.csv", revision="R9")  # snapshot 은 R1
    with pytest.raises(AgentError) as ei:
        compare_designs([a, b])
    assert ei.value.code == "E_REVISION_MISMATCH"
    with pytest.raises(AgentError):
        compare_designs([a, b], require_same_revision_policy=False)  # 정책과 무관하게 거부


def test_currency_and_base_date_mismatch_rejected() -> None:
    a = _candidate("A", "asm_a_snapshot.csv")
    b = _candidate("B", "asm_b_snapshot.csv", currency="USD")  # cost 는 KRW
    with pytest.raises(AgentError) as ei:
        compare_designs([a, b])
    assert ei.value.code == "E_REVISION_MISMATCH"
    b2 = _candidate("B", "asm_b_snapshot.csv", base_date="2026-01-01")
    with pytest.raises(AgentError) as ei2:
        compare_designs([a, b2])
    assert ei2.value.code == "E_REVISION_MISMATCH"
    # cost 없는 후보가 다른 통화를 선언하면 후보 간 불일치
    b3 = _candidate("B", "asm_b_snapshot.csv", with_cost=False, currency="USD", base_date="2026-09-01")
    with pytest.raises(AgentError) as ei3:
        compare_designs([a, b3])
    assert ei3.value.code == "E_REVISION_MISMATCH"


def test_cost_without_currency_declaration_rejected() -> None:
    a = _candidate("A", "asm_a_snapshot.csv")
    b = _candidate("B", "asm_b_snapshot.csv", currency=None)
    with pytest.raises(AgentError) as ei:
        compare_designs([a, b])
    assert ei.value.code == "E_INPUT_INVALID"


def test_missing_or_invalid_values_rejected() -> None:
    with pytest.raises(ValidationError):
        _candidate("A", "asm_a_snapshot.csv", revision="")
    with pytest.raises(ValidationError):
        _candidate("A", "asm_a_snapshot.csv", base_date="2026/09/01")
    with pytest.raises(ValidationError):
        _candidate("A", "asm_a_snapshot.csv", currency="원")
    a = _candidate("A", "asm_a_snapshot.csv")
    with pytest.raises(AgentError) as ei:
        compare_designs([a])
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        compare_designs([a, _candidate("A", "asm_b_snapshot.csv")])
    assert ei2.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei3:
        compare_designs([a, _candidate("B", "asm_b_snapshot.csv")], baseline_id="C")
    assert ei3.value.code == "E_INPUT_INVALID"


def test_performance_units_and_deltas() -> None:
    pa = {"삽입력": Quantity(value=52.0, unit="N", value_type="predicted", model_run_id="run-1")}
    pb = {
        "삽입력": Quantity(value=48.5, unit="N", value_type="predicted", model_run_id="run-1"),
        "유지력": Quantity.missing("N"),
    }
    a = _candidate("A", "asm_a_snapshot.csv", performance=pa)
    b = _candidate("B", "asm_b_snapshot.csv", performance=pb)
    cmp = compare_designs([a, b], baseline_id="B")
    d = {x.metric: x for x in cmp.deltas}
    assert d["performance:삽입력"].delta.value == pytest.approx(3.5)
    assert d["performance:삽입력"].delta.assumption is True  # 예측값 기반
    assert d["performance:유지력"].delta.is_missing()
    assert any("performance: 유지력 없음" == u for u in cmp.rows[0].unknowns)
    assert any("performance: 유지력 MISSING" == u for u in cmp.rows[1].unknowns)
    # 단위 불일치
    b_bad = _candidate(
        "B",
        "asm_b_snapshot.csv",
        performance={"삽입력": Quantity(value=4.9, unit="kgf", value_type="source", source_locator="x")},
    )
    with pytest.raises(AgentError) as ei:
        compare_designs([a, b_bad])
    assert ei.value.code == "E_REVISION_MISMATCH"


def test_no_cost_candidates_still_compare() -> None:
    a = _candidate("A", "asm_a_snapshot.csv", with_cost=False)
    b = _candidate("B", "asm_b_snapshot.csv", with_cost=False)
    cmp = compare_designs([a, b])
    assert cmp.consistent is True and cmp.currency is None
    assert cmp.rows[0].cost_complete is None and cmp.rows[0].unit_cost.is_missing()
    assert {d.metric: d for d in cmp.deltas}["unit_cost"].delta.is_missing()
    assert any(u == "cost: cost_breakdown 없음" for u in cmp.rows[0].unknowns)


def _make_dir(
    tmp_path: Path,
    cid: str,
    fixture: str,
    *,
    meta: dict | None = None,
    with_mass: bool = True,
    with_cost: bool = True,
) -> Path:
    d = tmp_path / cid
    d.mkdir()
    snap = import_snapshot(FIXTURES / fixture)
    write_snapshot(snap, d / "cad_snapshot.json")
    mass = compute_mass(snap)
    if with_mass:
        atomic_write_json(d / "mass_result.json", mass.model_dump(mode="json"))
    if with_cost:
        recipes = load_recipes(FIXTURES / "cost_recipes.json")
        cube = next(i for i in mass.items if i.reference_id == "PART_CUBE")
        atomic_write_json(
            d / "cost_breakdown.json",
            compute_cost(cube, recipes["injection_synthetic_pp"]).model_dump(mode="json"),
        )
    if meta is not None:
        (d / "candidate.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return d


def test_load_candidate_dir_defaults_and_meta(tmp_path: Path) -> None:
    da = _make_dir(tmp_path, "A", "asm_a_snapshot.csv")
    db = _make_dir(
        tmp_path,
        "B",
        "asm_b_snapshot.csv",
        meta={
            "label": "B안 (경량화)",
            "performance": {
                "삽입력": {"value": 48.5, "unit": "N", "value_type": "predicted", "model_run_id": "r1"}
            },
            "constraints": {"강도": True},
        },
        with_mass=False,
    )
    a = load_candidate_dir(da, candidate_id="A")
    b = load_candidate_dir(db, candidate_id="B")
    assert a.revision == "R1" and a.currency == "KRW" and a.base_date == "2026-09-01"
    assert isinstance(b.mass, MassResult) and b.mass.total.value == pytest.approx(
        3.5425
    )  # snapshot 에서 계산
    assert (
        b.label == "B안 (경량화)"
        and b.performance["삽입력"].value == 48.5
        and b.constraints == {"강도": True}
    )
    cmp = compare_designs([a, b])
    assert cmp.consistent


def test_load_candidate_dir_rejects_bad_meta(tmp_path: Path) -> None:
    d = _make_dir(tmp_path, "A", "asm_a_snapshot.csv", meta={"revision": ""})
    with pytest.raises(AgentError) as ei:
        load_candidate_dir(d, candidate_id="A")
    assert ei.value.code == "E_INPUT_INVALID"
    d2 = _make_dir(tmp_path, "B", "asm_b_snapshot.csv", meta={"base_date": "어제"})
    with pytest.raises(AgentError) as ei2:
        load_candidate_dir(d2, candidate_id="B")
    assert ei2.value.code == "E_INPUT_INVALID"
    empty = tmp_path / "C"
    empty.mkdir()
    with pytest.raises(AgentError) as ei3:
        load_candidate_dir(empty, candidate_id="C")
    assert ei3.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError):
        load_candidate_dir(tmp_path / "없는폴더", candidate_id="D")
