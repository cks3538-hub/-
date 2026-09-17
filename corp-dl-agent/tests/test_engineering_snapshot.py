"""CAD snapshot 수입 시험: 인코딩(UTF-8-SIG/UTF-8/CP949), 한글 경로, 검증 거부, mapping, JSON 왕복."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from corp_dl_agent.common import read_json
from corp_dl_agent.engineering.cad_snapshot import (
    CadSnapshot,
    import_snapshot,
    load_snapshot,
    summarize_snapshot,
    write_snapshot,
)
from corp_dl_agent.errors import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"
HEADER = (
    "document_id,revision,configuration_id,occurrence_path,parent_path,reference_id,quantity,is_assembly,"
    "geometry_status,material_id,density,density_unit,volume,volume_unit,mass,mass_unit\n"
)


def _csv(tmp_path: Path, name: str, body: str, encoding: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes((HEADER + body).encode(encoding))
    return p


def test_import_fixture_a_utf8_sig() -> None:
    snap = import_snapshot(FIXTURES / "asm_a_snapshot.csv")
    assert snap.encoding == "utf-8-sig"
    assert snap.synthetic is True
    assert len(snap.rows) == 9
    assert snap.error_issues() == []
    assert snap.revisions() == ["R1"]
    assert snap.document_ids() == ["DOC-ASM-A"]
    assert len(snap.source_hash) == 64
    cube = next(r for r in snap.rows if r.occurrence_path == "ROOT/ASM_A/PART_CUBE.1")
    assert cube.volume_m3 == pytest.approx(1e-3, abs=1e-18)
    assert cube.density_kg_m3 == 1000.0
    assert cube.mass_kg == 1.0
    assert cube.units == {"volume": "mm3", "density": "g/cm3", "mass": "kg"}
    assert cube.parameter_map == {"nominal_thickness_mm": 3.0}
    assert cube.extractor_version == "synthetic_generator/1.0"
    root = next(r for r in snap.rows if r.occurrence_path == "ROOT/ASM_A")
    assert root.is_assembly is True and root.parent_path is None
    skin = next(r for r in snap.rows if r.reference_id == "PART_SKIN")
    assert skin.geometry_status == "surface" and skin.surface_thickness_m is None
    assert skin.parameter_map["area"] == 200000.0 and skin.units["area"] == "mm2"
    summary = summarize_snapshot(snap)
    assert summary["n_rows"] == 9 and summary["n_references"] == 9
    assert summary["geometry_status_counts"] == {"solid": 6, "suppressed": 1, "unloaded": 1, "surface": 1}


def test_import_fixture_b_utf8() -> None:
    snap = import_snapshot(FIXTURES / "asm_b_snapshot.csv")
    assert snap.encoding == "utf-8"
    assert len(snap.rows) == 9
    bracket = next(r for r in snap.rows if r.reference_id == "PART_BRACKET")
    assert bracket.material_id == "PA66-GF30"
    assert bracket.density_kg_m3 == pytest.approx(1370.0)


def test_import_cp949_with_korean_values() -> None:
    snap = import_snapshot(FIXTURES / "asm_a_snapshot_cp949.csv")
    assert snap.encoding == "cp949"
    assert snap.rows[0].configuration_id == "기본형"
    assert snap.rows[1].parameter_map["비고"] == "합성 데이터"
    assert len(snap.rows) == 9


def test_import_from_korean_path_copy(tmp_path: Path) -> None:
    kdir = tmp_path / "한글 경로 테스트" / "설계 자료"
    kdir.mkdir(parents=True)
    target = kdir / "스냅샷 A (복사본).csv"
    shutil.copyfile(FIXTURES / "asm_a_snapshot.csv", target)
    snap = import_snapshot(target)
    assert "한글 경로 테스트" in snap.source_path
    assert len(snap.rows) == 9
    # 원본 fixture 는 수정되지 않았다 (hash 동일)
    assert snap.source_hash == import_snapshot(FIXTURES / "asm_a_snapshot.csv").source_hash


def test_duplicate_occurrence_rejected_and_kept_in_lenient(tmp_path: Path) -> None:
    body = (
        "D,R1,,ROOT/P.1,,P,1,false,solid,M,1.0,g/cm3,1000,mm3,,\n"
        "D,R1,,ROOT/P.1,,P,1,false,solid,M,1.0,g/cm3,1000,mm3,,\n"
    )
    p = _csv(tmp_path, "dup.csv", body)
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert ei.value.code == "E_INPUT_INVALID"
    assert any(e["code"] == "DUPLICATE_OCCURRENCE" for e in ei.value.details["errors"])
    snap = import_snapshot(p, strict=False)
    assert len(snap.rows) == 2  # 오류 행 자동 삭제 금지
    assert any(i.code == "DUPLICATE_OCCURRENCE" and i.level == "error" for i in snap.issues)


def test_unknown_geometry_status_rejected(tmp_path: Path) -> None:
    p = _csv(tmp_path, "geo.csv", "D,R1,,ROOT/P.1,,P,1,false,wireframe,M,1.0,g/cm3,1000,mm3,,\n")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert any(e["code"] == "UNKNOWN_GEOMETRY_STATUS" for e in ei.value.details["errors"])


def test_volume_without_unit_rejected(tmp_path: Path) -> None:
    p = _csv(tmp_path, "nounit.csv", "D,R1,,ROOT/P.1,,P,1,false,solid,M,1.0,g/cm3,1000,,,\n")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    errs = ei.value.details["errors"]
    assert any(e["code"] == "UNIT_MISSING" and e["field"] == "volume" for e in errs)


def test_unknown_unit_and_negative_rejected(tmp_path: Path) -> None:
    body = "D,R1,,ROOT/P.1,,P,1,false,solid,M,1.0,g/cm3,1000,in3,,\nD,R1,,ROOT/P.2,,P,1,false,solid,M,1.0,g/cm3,-5,mm3,,\n"
    p = _csv(tmp_path, "bad.csv", body)
    snap = import_snapshot(p, strict=False)
    codes = {i.code for i in snap.issues if i.level == "error"}
    assert "UNIT_UNKNOWN" in codes and "NEGATIVE_VALUE" in codes
    assert snap.rows == []  # 두 행 모두 오류


def test_quantity_zero_and_missing_required_rejected(tmp_path: Path) -> None:
    body = "D,R1,,ROOT/P.1,,P,0,false,solid,M,1.0,g/cm3,1000,mm3,,\nD,,,ROOT/P.2,,P,1,false,solid,M,1.0,g/cm3,1000,mm3,,\n"
    p = _csv(tmp_path, "qty.csv", body)
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    errs = ei.value.details["errors"]
    assert any(e["field"] == "quantity" for e in errs)
    assert any(e["field"] == "revision" and e["row"] == 2 for e in errs)


def test_field_mapping_company_columns(tmp_path: Path) -> None:
    header = "Doc,Rev,Occurrence,Ref,Qty,Vol,VolUnit,Dens,DensUnit,ExtraCompanyColumn\n"
    body = "DOC-X,R3,ROOT/A.1,A,2,1000000,mm3,1.0,g/cm3,ignored\n"
    p = tmp_path / "company.csv"
    p.write_text(header + body, encoding="utf-8")
    mapping = {
        "document_id": "Doc",
        "revision": "Rev",
        "occurrence_path": "Occurrence",
        "reference_id": "Ref",
        "quantity": "Qty",
        "volume": "Vol",
        "volume_unit": "VolUnit",
        "density": "Dens",
        "density_unit": "DensUnit",
    }
    snap = import_snapshot(p, field_mapping=mapping)
    assert len(snap.rows) == 1
    r = snap.rows[0]
    assert r.document_id == "DOC-X" and r.revision == "R3" and r.quantity == 2
    assert r.volume_m3 == pytest.approx(1e-3) and r.density_kg_m3 == 1000.0
    assert any(i.code == "UNKNOWN_COLUMNS" and "ExtraCompanyColumn" in i.message for i in snap.issues)


def test_parent_not_found_is_warning_and_mixed_revision(tmp_path: Path) -> None:
    body = "D,R1,,ROOT/P.1,ROOT/NOPE,P,1,false,solid,M,1.0,g/cm3,1000,mm3,,\nD,R2,,ROOT/P.2,,P,1,false,solid,M,1.0,g/cm3,1000,mm3,,\n"
    p = _csv(tmp_path, "warn.csv", body)
    snap = import_snapshot(p)
    codes = {i.code for i in snap.issues}
    assert "PARENT_NOT_FOUND" in codes and "MIXED_REVISION" in codes
    assert snap.error_issues() == []


def test_json_roundtrip_and_json_import(tmp_path: Path) -> None:
    snap = import_snapshot(FIXTURES / "asm_a_snapshot.csv")
    out = tmp_path / "cad_snapshot.json"
    write_snapshot(snap, out)
    loaded = load_snapshot(out)
    assert isinstance(loaded, CadSnapshot)
    assert loaded.model_dump() == snap.model_dump()
    data = read_json(out)
    assert data["synthetic"] is True and len(data["rows"]) == 9
    # JSON 자체를 다시 수입 (SI 값 그대로)
    re_imported = import_snapshot(out)
    assert len(re_imported.rows) == 9 and re_imported.synthetic is True
    assert re_imported.rows[1].volume_m3 == pytest.approx(1e-3)


def test_json_rows_with_extra_field_rejected(tmp_path: Path) -> None:
    rows = [
        {"document_id": "D", "revision": "R1", "occurrence_path": "ROOT/P.1", "reference_id": "P", "volume_m3": 0.001, "density_kg_m3": 1000.0, "units": {"volume": "m3"}, "bogus": 1}
    ]
    p = tmp_path / "rows.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert any(e["code"] == "UNKNOWN_FIELD" for e in ei.value.details["errors"])


def test_json_negative_value_rejected(tmp_path: Path) -> None:
    rows = [{"document_id": "D", "revision": "R1", "occurrence_path": "ROOT/P.1", "reference_id": "P", "volume_m3": -0.001, "density_kg_m3": 1000.0}]
    p = tmp_path / "neg.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert any(e["code"] == "NEGATIVE_VALUE" for e in ei.value.details["errors"])


def test_unsupported_format_and_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "model.CATPart"
    p.write_bytes(b"binary")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError) as ei2:
        import_snapshot(tmp_path / "없는파일.csv")
    assert ei2.value.code == "E_INPUT_INVALID"


def test_empty_csv_rejected(tmp_path: Path) -> None:
    p = _csv(tmp_path, "empty.csv", "")
    with pytest.raises(AgentError) as ei:
        import_snapshot(p)
    assert any(e["code"] == "EMPTY" for e in ei.value.details["errors"])


def test_load_snapshot_schema_invalid(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"rows": [], "source_path": "x"}), encoding="utf-8")
    with pytest.raises(AgentError) as ei:
        load_snapshot(p)
    assert ei.value.code == "E_SCHEMA_INVALID"
