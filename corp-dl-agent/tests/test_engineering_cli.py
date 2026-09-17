"""CLI 시험: cad import / cad extract / design mass|cost|compare 가 실제 JSON 산출물을 만든다."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from corp_dl_agent.cli import main
from corp_dl_agent.common import read_json
from corp_dl_agent.errors import EXIT_NOT_SUPPORTED, EXIT_VALIDATION

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cad"


def _common(tmp_path: Path, *extra_roots: Path) -> list[str]:
    roots = ",".join(str(p) for p in (FIXTURES, *extra_roots))
    return ["--set", f"paths.data_root={tmp_path}", "--set", f"paths.input_roots=[{roots}]"]


def test_cad_import_writes_snapshot_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out" / "cad_snapshot.json"
    rc = main(
        [
            "cad",
            "import",
            "--input",
            str(FIXTURES / "asm_a_snapshot.csv"),
            "--output",
            str(out),
            "--json",
            *_common(tmp_path),
        ]
    )
    assert rc == 0
    assert out.is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["summary"]["n_rows"] == 9
    assert payload["summary"]["encoding"] == "utf-8-sig" and payload["summary"]["synthetic"] is True
    data = read_json(out)
    assert len(data["rows"]) == 9 and data["synthetic"] is True
    assert data["rows"][1]["volume_m3"] == pytest.approx(1e-3)


def test_cad_import_with_mass_output_and_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cad_snapshot.json"
    mass = tmp_path / "mass_result.json"
    rc = main(
        [
            "cad",
            "import",
            "--input",
            str(FIXTURES / "asm_b_snapshot.csv"),
            "--output",
            str(out),
            "--mass-output",
            str(mass),
            *_common(tmp_path),
        ]
    )
    assert rc == 0
    text = capsys.readouterr().out
    assert "snapshot 저장" in text and "mass_result 저장" in text
    m = read_json(mass)
    assert m["total"]["value"] == pytest.approx(3.5425) and m["complete"] is False
    assert m["total"]["calculation_version"] == "mass-cost-4.0"


def test_cad_import_from_korean_path(tmp_path: Path) -> None:
    kdir = tmp_path / "한글 폴더" / "입력 자료"
    kdir.mkdir(parents=True)
    src = kdir / "스냅샷 A.csv"
    shutil.copyfile(FIXTURES / "asm_a_snapshot_cp949.csv", src)
    out = tmp_path / "한글 출력" / "cad_snapshot.json"
    rc = main(["cad", "import", "--input", str(src), "--output", str(out), *_common(tmp_path)])
    assert rc == 0 and out.is_file()
    data = read_json(out)
    assert data["encoding"] == "cp949" and data["rows"][0]["configuration_id"] == "기본형"


def test_cad_import_mapping_and_invalid_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "company.csv"
    src.write_text(
        "Doc,Rev,Occ,Ref,Vol,VolUnit,Dens,DensUnit\nD,R1,ROOT/P.1,P,1000000,mm3,1.0,g/cm3\n", encoding="utf-8"
    )
    out = tmp_path / "snap.json"
    rc = main(
        [
            "cad",
            "import",
            "--input",
            str(src),
            "--output",
            str(out),
            "--mapping",
            "document_id=Doc",
            "--mapping",
            "revision=Rev",
            "--mapping",
            "occurrence_path=Occ",
            "--mapping",
            "reference_id=Ref",
            "--mapping",
            "volume=Vol",
            "--mapping",
            "volume_unit=VolUnit",
            "--mapping",
            "density=Dens",
            "--mapping",
            "density_unit=DensUnit",
            *_common(tmp_path),
        ]
    )
    assert rc == 0 and read_json(out)["rows"][0]["volume_m3"] == pytest.approx(1e-3)
    capsys.readouterr()
    dup = tmp_path / "dup.csv"
    dup.write_text(
        "document_id,revision,occurrence_path,reference_id,volume,volume_unit,density,density_unit\nD,R1,ROOT/P.1,P,1,mm3,1,g/cm3\nD,R1,ROOT/P.1,P,1,mm3,1,g/cm3\n",
        encoding="utf-8",
    )
    rc = main(
        [
            "cad",
            "import",
            "--input",
            str(dup),
            "--output",
            str(tmp_path / "x.json"),
            "--json",
            *_common(tmp_path),
        ]
    )
    assert rc == EXIT_VALIDATION
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False and err["error"]["code"] == "E_INPUT_INVALID"
    assert not (tmp_path / "x.json").exists()


def test_cad_import_rejects_path_outside_roots(
    tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    data_root = tmp_path_factory.mktemp("data")
    outside = tmp_path_factory.mktemp("outside")
    src = outside / "a.csv"
    shutil.copyfile(FIXTURES / "asm_a_snapshot.csv", src)
    rc = main(
        [
            "cad",
            "import",
            "--input",
            str(src),
            "--output",
            str(data_root / "s.json"),
            "--set",
            f"paths.data_root={data_root}",
        ]
    )
    assert rc == EXIT_VALIDATION
    assert "E_PATH_OUTSIDE_ROOT" in capsys.readouterr().err


def test_cad_extract_not_supported_inactive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        ["cad", "extract", "--adapter", "catia_v5_com", "--json", "--set", f"paths.data_root={tmp_path}"]
    )
    assert rc == EXIT_NOT_SUPPORTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_NOT_SUPPORTED"
    assert payload["error"]["details"]["status"] == "INACTIVE"
    assert payload["error"]["details"]["status_record"]["status"] == "BLOCKED"
    assert "cad import" in payload["error"]["details"]["fallback"]
    rc2 = main(["cad", "extract", "--adapter", "3dexperience", "--set", f"paths.data_root={tmp_path}"])
    assert rc2 == EXIT_NOT_SUPPORTED
    assert "E_NOT_SUPPORTED" in capsys.readouterr().err


def test_help_is_korean(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        main(["cad", "import", "--help"])
    assert ei.value.code == 0
    assert "snapshot" in capsys.readouterr().out
    with pytest.raises(SystemExit) as ei2:
        main(["design", "compare", "--help"])
    assert ei2.value.code == 0
    assert "후보" in capsys.readouterr().out
    assert main(["cad"]) == 2
    assert main(["design"]) == 2


def _build_candidate(tmp_path: Path, cid: str, fixture: str, occurrence: str) -> Path:
    d = tmp_path / cid
    common = _common(tmp_path)
    assert (
        main(
            [
                "cad",
                "import",
                "--input",
                str(FIXTURES / fixture),
                "--output",
                str(d / "cad_snapshot.json"),
                *common,
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "design",
                "mass",
                "--snapshot",
                str(d / "cad_snapshot.json"),
                "--output",
                str(d / "mass_result.json"),
                *common,
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "design",
                "cost",
                "--mass",
                str(d / "mass_result.json"),
                "--recipes",
                str(FIXTURES / "cost_recipes.json"),
                "--recipe",
                "injection_synthetic_pp",
                "--occurrence",
                occurrence,
                "--output",
                str(d / "cost_breakdown.json"),
                *common,
            ]
        )
        == 0
    )
    return d


def test_design_pipeline_creates_comparison_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    da = _build_candidate(tmp_path, "A", "asm_a_snapshot.csv", "ROOT/ASM_A/PART_CUBE.1")
    db = _build_candidate(tmp_path, "B", "asm_b_snapshot.csv", "ROOT/ASM_B/PART_CUBE.1")
    (db / "candidate.json").write_text(
        json.dumps({"label": "B안 경량화", "constraints": {"강도 조건": None}}, ensure_ascii=False),
        encoding="utf-8",
    )
    capsys.readouterr()
    out = tmp_path / "design_comparison.json"
    rc = main(
        [
            "design",
            "compare",
            "--candidate",
            f"A={da}",
            "--candidate",
            f"B={db}",
            "--output",
            str(out),
            "--json",
            *_common(tmp_path),
        ]
    )
    assert rc == 0 and out.is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["comparison"]["consistent"] is True
    cmp = read_json(out)
    assert [r["candidate_id"] for r in cmp["rows"]] == ["A", "B"]
    assert cmp["rows"][0]["mass_total"]["value"] == pytest.approx(4.675)
    assert cmp["rows"][1]["mass_total"]["value"] == pytest.approx(3.5425)
    assert cmp["rows"][1]["label"] == "B안 경량화" and cmp["rows"][1]["constraints_ok"] is None
    assert cmp["rows"][0]["unit_cost"]["value"] == pytest.approx(3782.8947368421054)
    deltas = {d["metric"]: d for d in cmp["deltas"]}
    assert deltas["mass_total"]["delta"]["value"] == pytest.approx(3.5425 - 4.675)
    assert deltas["unit_cost"]["delta"]["value"] == pytest.approx(-550.0)
    assert cmp["revision"] == "R1" and cmp["currency"] == "KRW" and cmp["base_date"] == "2026-09-01"
    assert cmp["synthetic"] is True
    # 텍스트 출력도 동작
    rc2 = main(
        [
            "design",
            "compare",
            "--candidate",
            f"A={da}",
            "--candidate",
            f"B={db}",
            "--output",
            str(out),
            *_common(tmp_path),
        ]
    )
    assert rc2 == 0 and "design_comparison 저장" in capsys.readouterr().out


def test_design_compare_rejects_revision_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    da = _build_candidate(tmp_path, "A", "asm_a_snapshot.csv", "ROOT/ASM_A/PART_CUBE.1")
    db = _build_candidate(tmp_path, "B", "asm_b_snapshot.csv", "ROOT/ASM_B/PART_CUBE.1")
    # B 의 snapshot/mass 를 R2 로 바꾼다 (mass_result 를 지워 재계산)
    snap = read_json(db / "cad_snapshot.json")
    for r in snap["rows"]:
        r["revision"] = "R2"
    (db / "cad_snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    (db / "mass_result.json").unlink()
    capsys.readouterr()
    out = tmp_path / "cmp.json"
    rc = main(
        [
            "design",
            "compare",
            "--candidate",
            f"A={da}",
            "--candidate",
            f"B={db}",
            "--output",
            str(out),
            "--json",
            *_common(tmp_path),
        ]
    )
    assert rc == EXIT_VALIDATION
    err = json.loads(capsys.readouterr().out)
    assert err["error"]["code"] == "E_REVISION_MISMATCH"
    assert not out.exists()
    rc2 = main(
        [
            "design",
            "compare",
            "--candidate",
            f"A={da}",
            "--candidate",
            f"B={db}",
            "--output",
            str(out),
            "--allow-revision-mismatch",
            *_common(tmp_path),
        ]
    )
    assert rc2 == 0
    cmp = read_json(out)
    assert cmp["consistent"] is False and any("revision" in w for w in cmp["warnings"])


def test_design_cost_currency_mismatch_and_missing_recipe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    da = _build_candidate(tmp_path, "A", "asm_a_snapshot.csv", "ROOT/ASM_A/PART_CUBE.1")
    capsys.readouterr()
    base = [
        "design",
        "cost",
        "--mass",
        str(da / "mass_result.json"),
        "--recipes",
        str(FIXTURES / "cost_recipes.json"),
        "--output",
        str(tmp_path / "c.json"),
    ]
    rc = main([*base, "--recipe", "injection_usd_2025", "--currency", "KRW", "--json", *_common(tmp_path)])
    assert rc == EXIT_VALIDATION
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_REVISION_MISMATCH"
    rc2 = main([*base, "--recipe", "없는레시피", "--json", *_common(tmp_path)])
    assert rc2 == EXIT_VALIDATION
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_INPUT_INVALID"
    rc3 = main([*base, "--recipe", "injection_missing_price", "--json", *_common(tmp_path)])
    assert rc3 == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cost"]["unit_cost"] is None and payload["cost"]["complete"] is False


def test_design_mass_with_scope_and_material_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "A"
    common = _common(tmp_path)
    assert (
        main(
            [
                "cad",
                "import",
                "--input",
                str(FIXTURES / "asm_a_snapshot.csv"),
                "--output",
                str(d / "cad_snapshot.json"),
                *common,
            ]
        )
        == 0
    )
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(
            {"include_paint": True, "paint_items": [{"name": "도장", "mass_kg": 0.05}]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    out = d / "mass_result.json"
    rc = main(
        [
            "design",
            "mass",
            "--snapshot",
            str(d / "cad_snapshot.json"),
            "--output",
            str(out),
            "--scope",
            str(scope),
            "--material-table",
            str(FIXTURES / "material_table.csv"),
            "--set",
            "cad.default_density_policy=use_material_table",
            "--json",
            *common,
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mass"]["total_kg"] == pytest.approx(4.788 + 0.05)
    assert payload["mass"]["assumption"] is True
    m = read_json(out)
    assert m["extra_items"][0]["category"] == "paint"
    assert m["scope_summary"]["paint"].startswith("도장: 포함")
