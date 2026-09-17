"""중량 계산 시험: 1.0 kg 검증, 수량 곱, 이중합산 금지, suppressed 제외, MISSING 처리, scope, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_dl_agent.engineering.cad_snapshot import CadSnapshot, CadSnapshotRow, SnapshotIssue, import_snapshot
from corp_dl_agent.engineering.mass import MassResult, MassScope, ScopeItem, compute_mass, load_material_table
from corp_dl_agent.errors import AgentError
from corp_dl_agent.version import CALCULATION_VERSION

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"


def _row(path: str, **kw: object) -> CadSnapshotRow:
    base: dict[str, object] = {
        "document_id": "D",
        "revision": "R1",
        "occurrence_path": path,
        "reference_id": path.rsplit("/", 1)[-1].split(".")[0],
        "units": {"volume": "m3", "density": "kg/m3"},
    }
    base.update(kw)
    return CadSnapshotRow(**base)  # type: ignore[arg-type]


def _snap(rows: list[CadSnapshotRow], issues: list[SnapshotIssue] | None = None) -> CadSnapshot:
    return CadSnapshot(
        rows=rows, source_path="mem://test.csv", source_hash="0" * 64, extracted_at="2026-09-01T00:00:00+00:00", extractor_version="t", issues=issues or [], synthetic=True
    )


@pytest.fixture(scope="module")
def mass_a() -> MassResult:
    return compute_mass(import_snapshot(FIXTURES / "asm_a_snapshot.csv"))


def test_known_part_is_one_kg(mass_a: MassResult) -> None:
    it = mass_a.item("ROOT/ASM_A/PART_CUBE.1")
    assert it.status == "ok"
    assert it.unit_mass.value == pytest.approx(1.0, abs=1e-12)
    assert it.unit_mass.unit == "kg"
    assert it.unit_mass.value_type == "calculated"
    assert it.unit_mass.calculation_version == CALCULATION_VERSION
    assert it.unit_mass.assumption is False
    assert it.unit_mass.source_locator is not None and it.unit_mass.source_locator.endswith("#ROOT/ASM_A/PART_CUBE.1")
    assert it.unit_mass.calculation_id == "mass:ROOT/ASM_A/PART_CUBE.1"
    assert it.cad_mass.value_type == "source" and it.cad_mass.value == 1.0
    assert it.mass_check == "match"


def test_quantity_multiplication_same_reference(mass_a: MassResult) -> None:
    cubes = [i for i in mass_a.items if i.reference_id == "PART_CUBE"]
    assert len(cubes) == 3
    assert mass_a.item("ROOT/ASM_A/PART_CUBE.3").quantity == 2
    assert mass_a.item("ROOT/ASM_A/PART_CUBE.3").total_mass.value == pytest.approx(2.0)
    assert sum(c.total_mass.value or 0 for c in cubes) == pytest.approx(4.0)
    assert mass_a.n_instances_counted == 5  # cube 4 + bracket 1


def test_assembly_node_not_double_counted(mass_a: MassResult) -> None:
    root = mass_a.item("ROOT/ASM_A")
    assert root.status == "assembly_node" and root.counted is False
    assert root.cad_mass.value == 4.675  # 참고값으로 보존
    assert root.unit_mass.is_missing()
    assert mass_a.total.value == pytest.approx(4.675)  # 4.675 + 4.675 가 아니다
    assert mass_a.total_modeled.value == pytest.approx(4.675)
    assert mass_a.total.value == pytest.approx(sum(i.total_mass.value or 0 for i in mass_a.items if i.counted))


def test_suppressed_excluded_not_missing(mass_a: MassResult) -> None:
    sup = mass_a.item("ROOT/ASM_A/PART_SUPPRESSED.1")
    assert sup.status == "suppressed" and sup.counted is False
    assert not any("PART_SUPPRESSED" in m for m in mass_a.missing)


def test_unloaded_no_density_surface_are_missing(mass_a: MassResult) -> None:
    assert mass_a.item("ROOT/ASM_A/PART_UNLOADED.1").status == "unloaded"
    assert mass_a.item("ROOT/ASM_A/PART_NODENSITY.1").status == "missing_density"
    assert mass_a.item("ROOT/ASM_A/PART_SKIN.1").status == "surface_no_thickness"
    for p in ("PART_UNLOADED.1", "PART_NODENSITY.1", "PART_SKIN.1"):
        it = mass_a.item(f"ROOT/ASM_A/{p}")
        assert it.unit_mass.is_missing() and it.total_mass.is_missing() and it.counted is False
    assert mass_a.complete is False
    assert len(mass_a.missing) == 3
    assert "누락 3건" in (mass_a.total.notes or "")
    assert mass_a.total.value_type == "calculated"
    assert mass_a.counts == {"assembly_node": 1, "ok": 4, "suppressed": 1, "unloaded": 1, "missing_density": 1, "surface_no_thickness": 1}


def test_bracket_cm3_kg_m3(mass_a: MassResult) -> None:
    br = mass_a.item("ROOT/ASM_A/PART_BRACKET.1")
    assert br.unit_mass.value == pytest.approx(0.675)
    assert br.mass_check == "not_available" and br.cad_mass.is_missing()


def test_fixture_b_known_total() -> None:
    mb = compute_mass(import_snapshot(FIXTURES / "asm_b_snapshot.csv"))
    assert mb.total.value == pytest.approx(3.5425)
    assert mb.item("ROOT/ASM_B/PART_CUBE.1").unit_mass.value == pytest.approx(0.8)
    assert mb.item("ROOT/ASM_B/PART_BRACKET.1").unit_mass.value == pytest.approx(0.3425)
    assert mb.complete is False and mb.synthetic is True


def test_material_table_policy_marks_assumption() -> None:
    snap = import_snapshot(FIXTURES / "asm_a_snapshot.csv")
    table = load_material_table(str(FIXTURES / "material_table.csv"))
    assert table["PP-GF30"] == pytest.approx(1130.0)
    res = compute_mass(snap, density_policy="use_material_table", material_table=table, material_table_path="material_table.csv")
    it = res.item("ROOT/ASM_A/PART_NODENSITY.1")
    assert it.status == "ok"
    assert it.unit_mass.value == pytest.approx(0.113)
    assert it.unit_mass.assumption is True and it.unit_mass.value_type == "assumed"
    assert "재료표" in (it.unit_mass.notes or "") or "밀도 기본값" in (it.unit_mass.notes or "")
    assert res.total.value == pytest.approx(4.788)
    assert res.total.assumption is True
    assert len(res.missing) == 2
    # reject 정책에서는 재료표가 있어도 사용하지 않는다
    res2 = compute_mass(snap, density_policy="reject", material_table=table)
    assert res2.item("ROOT/ASM_A/PART_NODENSITY.1").status == "missing_density"


def test_surface_with_thickness_and_area_is_assumed() -> None:
    rows = [
        _row("ROOT/SKIN.1", geometry_status="surface", density_kg_m3=1200.0, surface_thickness_m=0.002, parameter_map={"area": 200000.0}, units={"area": "mm2", "density": "kg/m3"}),
        _row("ROOT/SKIN.2", geometry_status="surface", density_kg_m3=1200.0, surface_thickness_m=0.002),
        _row("ROOT/SKIN.3", geometry_status="surface", surface_thickness_m=0.002, parameter_map={"area": 200000.0}, units={"area": "mm2"}),
    ]
    res = compute_mass(_snap(rows))
    ok = res.item("ROOT/SKIN.1")
    assert ok.status == "ok" and ok.unit_mass.value == pytest.approx(0.48)
    assert ok.unit_mass.assumption is True and "surface 근사" in (ok.unit_mass.notes or "")
    assert res.item("ROOT/SKIN.2").status == "surface_no_area"
    assert res.item("ROOT/SKIN.3").status == "missing_density"
    assert res.total.assumption is True and res.complete is False


def test_cad_mass_conflict_marks_conflict() -> None:
    rows = [_row("ROOT/P.1", volume_m3=0.001, density_kg_m3=1000.0, mass_kg=1.2)]
    res = compute_mass(_snap(rows))
    it = res.item("ROOT/P.1")
    assert it.status == "conflict_with_cad" and it.mass_check == "mismatch"
    assert it.unit_mass.value_type == "conflict" and it.unit_mass.value is None
    assert "1.2" in (it.unit_mass.notes or "")
    assert res.complete is False and res.total.value == 0.0
    # 허용오차 안 (반올림) 은 match
    res2 = compute_mass(_snap([_row("ROOT/P.1", volume_m3=0.001, density_kg_m3=1000.0, mass_kg=1.0004)]))
    assert res2.item("ROOT/P.1").mass_check == "match"


def test_sub_assembly_quantity_propagates() -> None:
    rows = [
        _row("ROOT/ASM", is_assembly=True),
        _row("ROOT/ASM/SUB.1", parent_path="ROOT/ASM", quantity=2, is_assembly=True),
        _row("ROOT/ASM/SUB.1/P.1", parent_path="ROOT/ASM/SUB.1", quantity=3, volume_m3=0.001, density_kg_m3=1000.0),
    ]
    res = compute_mass(_snap(rows))
    p = res.item("ROOT/ASM/SUB.1/P.1")
    assert p.quantity == 3 and p.effective_quantity == 6
    assert p.total_mass.value == pytest.approx(6.0)
    assert res.total.value == pytest.approx(6.0) and res.complete is True
    assert res.item("ROOT/ASM/SUB.1").status == "assembly_node"


def test_parent_with_children_but_not_flagged_assembly_is_excluded() -> None:
    rows = [
        _row("ROOT/X", volume_m3=0.005, density_kg_m3=1000.0, mass_kg=5.0),
        _row("ROOT/X/P.1", parent_path="ROOT/X", volume_m3=0.001, density_kg_m3=1000.0),
    ]
    res = compute_mass(_snap(rows))
    assert res.item("ROOT/X").status == "assembly_node"
    assert res.total.value == pytest.approx(1.0)
    assert any("이중합산" in w for w in res.warnings)
    assert any("불일치" in w for w in res.warnings)  # 자식 합계 1.0 vs CAD 총량 5.0


def test_assembly_without_children_is_missing() -> None:
    res = compute_mass(_snap([_row("ROOT/ASM", is_assembly=True, mass_kg=3.0)]))
    assert res.complete is False
    assert any("assembly_node_without_children" in m for m in res.missing)
    assert res.total.value == 0.0


def test_scope_items_and_missing_scope() -> None:
    snap = import_snapshot(FIXTURES / "asm_a_snapshot.csv")
    scope = MassScope(
        include_paint=True,
        paint_items=[ScopeItem(name="도장 (외관면)", mass_kg=0.05, source_locator="paint_spec.xlsx#B3")],
        include_foam=True,  # 항목 없음 -> MISSING
        adhesive_items=[ScopeItem(name="접착제", mass_kg=0.01)],  # include_adhesive=False -> 합산 안 함
    )
    res = compute_mass(snap, scope=scope)
    assert res.total_modeled.value == pytest.approx(4.675)
    assert res.total.value == pytest.approx(4.725)
    assert [e.category for e in res.extra_items] == ["paint"]
    assert res.extra_items[0].mass.value_type == "source"
    assert any(m.startswith("scope:foam") for m in res.missing)
    assert any("include_adhesive=False" in w for w in res.warnings)
    assert res.scope_summary["paint"].startswith("도장: 포함")
    assert res.scope_summary["foam"].endswith("(MISSING)")
    assert res.scope_summary["adhesive"] == "접착제: 미포함"
    assert res.scope_summary["purchased"] == "미모델링 구매품: 미포함"
    assumed = MassScope(include_purchased=True, purchased_items=[ScopeItem(name="클립", mass_kg=0.002, assumption=True)])
    res2 = compute_mass(snap, scope=assumed)
    assert res2.total.assumption is True and res2.extra_items[0].mass.value_type == "assumed"


def test_default_scope_excludes_everything(mass_a: MassResult) -> None:
    assert mass_a.extra_items == []
    assert all(v.endswith("미포함") for k, v in mass_a.scope_summary.items() if k != "cad_modeled")


def test_snapshot_with_errors_refused() -> None:
    snap = _snap([_row("ROOT/P.1", volume_m3=0.001, density_kg_m3=1000.0)], issues=[SnapshotIssue(level="error", code="X", message="x")])
    with pytest.raises(AgentError) as ei:
        compute_mass(snap)
    assert ei.value.code == "E_INPUT_INVALID"


def test_missing_volume_and_geometry_missing() -> None:
    rows = [_row("ROOT/P.1", density_kg_m3=1000.0), _row("ROOT/P.2", geometry_status="missing")]
    res = compute_mass(_snap(rows))
    assert res.item("ROOT/P.1").status == "missing_volume"
    assert res.item("ROOT/P.2").status == "missing_geometry"
    assert res.complete is False


def test_mass_result_json_roundtrip(mass_a: MassResult, tmp_path: Path) -> None:
    from corp_dl_agent.common import atomic_write_json, read_json, validate_strict

    p = tmp_path / "mass_result.json"
    atomic_write_json(p, mass_a.model_dump(mode="json"))
    loaded = validate_strict(MassResult, read_json(p))
    assert loaded.total.value == mass_a.total.value
    assert loaded.model_dump() == mass_a.model_dump()


def test_material_table_errors(tmp_path: Path) -> None:
    p = tmp_path / "mat.csv"
    p.write_text("material_id,density,density_unit\nX,,g/cm3\n", encoding="utf-8")
    with pytest.raises(AgentError):
        load_material_table(str(p))
    p.write_text("material_id,density,density_unit\nX,1.0,lb/ft3\n", encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        load_material_table(str(p))
    assert ei.value.code == "E_UNIT_MISMATCH"
