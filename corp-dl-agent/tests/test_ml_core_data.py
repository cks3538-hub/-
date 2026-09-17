"""데이터 로딩·검증 시험: 인코딩(utf-8/utf-8-sig/cp949 한글 헤더), ID 문자열 보존, 오류 행 보고(삭제 금지), 한글 경로, root 검사."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.common import validate_strict
from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.data import DataReport, detect_encoding, load_dataset
from corp_dl_agent.ml.taskspec import TaskSpec, load_taskspec, taskspec_from_dict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"
HEADER = "id,grp,x1,x2,c1,y,post\n"


def make_spec(path: Path, **overrides: Any) -> TaskSpec:
    d: dict[str, Any] = {
        "task_id": "t",
        "task_type": "regression",
        "data_path": str(path),
        "target": "y",
        "numeric_features": ["x1", "x2"],
        "categorical_features": ["c1"],
        "units": {"y": "N"},
        "id_column": "id",
        "group_column": "grp",
        "metric": "mae",
        "direction": "min",
        "data_origin": "synthetic",
        "excluded_columns": ["post"],
    }
    d.update(overrides)
    return taskspec_from_dict(d)


def write_csv(
    tmp_path: Path, body: str, *, name: str = "data.csv", encoding: str = "utf-8", header: str = HEADER
) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((header + body).encode(encoding))
    return p


GOOD = "001,g1,1.0,2.0,a,10.0,x\n002,g1,1.5,2.5,b,11.0,x\n003,g2,2.0,3.0,a,12.5,x\n004,g3,2.5,3.5,c,14.0,x\n"


def issue_codes(exc: AgentError) -> dict[str, dict[str, Any]]:
    return {i["code"]: i for i in exc.details["issues"]}


def test_fixture_regression_utf8_and_ids_preserved() -> None:
    spec = load_taskspec(FIXTURES / "task_regression.yaml")
    df, rep = load_dataset(spec, roots=[FIXTURES])
    assert rep.encoding_detected == "utf-8" and rep.encodings_tried == ["utf-8"]
    assert rep.passed and rep.errors() == []
    assert rep.n_rows == 600 and rep.n_groups == 40
    assert (
        df[spec.id_column].iloc[0] == "000001"
        and df[spec.id_column].dtype == object
        or str(df[spec.id_column].dtype).startswith("str")
    )
    assert all(len(v) == 6 for v in df[spec.id_column].tolist())
    assert rep.target_stats["unit"] == "N" and rep.target_stats["count"] == 600
    assert rep.synthetic is True and rep.data_origin == "synthetic"
    assert str(df["thickness_mm"].dtype) == "float64"
    assert rep.dtypes["specimen_id"] != "float64"
    # 사후 결과 열은 남아 있으나(삭제 금지) feature 가 아니다
    assert "post_retention_force_n" in df.columns


def test_fixture_classification_utf8_sig() -> None:
    spec = load_taskspec(FIXTURES / "task_classification.yaml")
    raw = (FIXTURES / "clip_classification.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    df, rep = load_dataset(spec, roots=[FIXTURES])
    assert rep.encoding_detected == "utf-8-sig"
    assert rep.columns[0] == "specimen_id"  # BOM 이 헤더에 남지 않는다
    assert set(df[spec.target].unique().tolist()) == {0, 1}
    assert rep.target_stats["positives"] + rep.target_stats["negatives"] == 600  # type: ignore[operator]
    assert str(df[spec.time_column].dtype).startswith("datetime64")


def test_cp949_korean_header_and_values(tmp_path: Path) -> None:
    header = "시편ID,설계계열,두께,홀직경,재질,삽입력,사후결과\n"
    body = "007,계열A,1.0,5.0,나일론,10.0,1\n008,계열A,1.2,5.5,나일론,11.0,1\n009,계열B,1.4,6.0,폴리,12.0,1\n010,계열C,1.6,6.5,폴리,13.0,1\n"
    p = write_csv(tmp_path / "한글 폴더", body, name="데이터.csv", encoding="cp949", header=header)
    spec = make_spec(
        p,
        target="삽입력",
        numeric_features=["두께", "홀직경"],
        categorical_features=["재질"],
        units={"삽입력": "N", "두께": "mm"},
        id_column="시편ID",
        group_column="설계계열",
        excluded_columns=["사후결과"],
    )
    df, rep = load_dataset(spec, roots=[tmp_path])
    assert rep.encoding_detected == "cp949" and rep.encodings_tried == ["utf-8", "cp949"]
    assert rep.passed
    assert df["시편ID"].tolist() == ["007", "008", "009", "010"]
    assert df["재질"].tolist() == ["나일론", "나일론", "폴리", "폴리"]
    assert rep.target_stats["unit"] == "N"


def test_detect_encoding_failure() -> None:
    with pytest.raises(AgentError) as ei:
        detect_encoding(b"\xff\xfe\x00\x00\x81\x81\xff")
    assert ei.value.code == "E_INPUT_INVALID"


def test_missing_target_rows_reported_not_deleted(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,10.0,x\n002,g1,1.5,2.5,b,,x\n003,g2,2.0,3.0,a,12.5,x\n004,g3,2.5,3.5,c,,x\n",
    )
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    assert ei.value.code == "E_INPUT_INVALID"
    codes = issue_codes(ei.value)
    assert codes["MISSING_TARGET"]["count"] == 2
    rows = codes["MISSING_TARGET"]["rows"]
    assert [(r["index"], r["line"], r["id"]) for r in rows] == [(1, 3, "002"), (3, 5, "004")]
    assert ei.value.details["report"]["passed"] is False
    assert ei.value.details["report"]["n_rows"] == 4  # 오류 행 삭제 없음


def test_duplicate_ids(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,10.0,x\n001,g1,1.5,2.5,b,11.0,x\n003,g2,2.0,3.0,a,12.5,x\n004,g3,2.5,3.5,c,14.0,x\n",
    )
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert codes["DUPLICATE_ID"]["count"] == 2 and [r["index"] for r in codes["DUPLICATE_ID"]["rows"]] == [
        0,
        1,
    ]


def test_nan_inf_and_non_numeric(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,inf,2.0,a,10.0,x\n002,g1,1.5,NaN,b,11.0,x\n003,g2,abc,3.0,a,12.5,x\n004,g3,2.5,3.5,c,-inf,x\n",
    )
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert codes["NON_FINITE"]["count"] >= 1 and codes["NON_NUMERIC"]["count"] == 1
    nf_rows = {
        (i["column"], r["index"])
        for i in ei.value.details["issues"]
        if i["code"] == "NON_FINITE"
        for r in i["rows"]
    }
    assert ("x1", 0) in nf_rows and ("x2", 1) in nf_rows and ("y", 3) in nf_rows
    assert [r["index"] for r in codes["NON_NUMERIC"]["rows"]] == [2]


def test_feature_equals_target(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,10.0,2.0,a,10.0,x\n002,g1,11.0,2.5,b,11.0,x\n003,g2,12.5,3.0,a,12.5,x\n004,g3,14.0,3.5,c,14.0,x\n",
    )
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert codes["FEATURE_EQUALS_TARGET"]["column"] == "x1"


def test_group_missing_and_id_missing(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,10.0,x\n002,,1.5,2.5,b,11.0,x\n,g2,2.0,3.0,a,12.5,x\n004,g3,2.5,3.5,c,14.0,x\n",
    )
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert [r["index"] for r in codes["MISSING_GROUP"]["rows"]] == [1]
    assert [r["index"] for r in codes["MISSING_ID"]["rows"]] == [2]


def test_missing_column_and_categorical_missing(tmp_path: Path) -> None:
    p = write_csv(tmp_path, GOOD)
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p, numeric_features=["x1", "x9"]), roots=[tmp_path])
    assert "MISSING_COLUMN" in issue_codes(ei.value)
    p2 = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,,10.0,x\n002,g1,1.5,2.5,b,11.0,x\n003,g2,2.0,3.0,a,12.5,x\n004,g3,2.5,3.5,c,14.0,x\n",
        name="d2.csv",
    )
    with pytest.raises(AgentError) as ei2:
        load_dataset(make_spec(p2), roots=[tmp_path])
    codes = issue_codes(ei2.value)
    assert codes["MISSING_VALUE"]["column"] == "c1" and [
        r["index"] for r in codes["MISSING_VALUE"]["rows"]
    ] == [0]


def test_classification_target_rules(tmp_path: Path) -> None:
    p = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,1,x\n002,g1,1.5,2.5,b,0,x\n003,g2,2.0,3.0,a,maybe,x\n004,g3,2.5,3.5,c,2,x\n",
    )
    spec = make_spec(p, task_type="binary_classification", metric="f1", direction="max", units={})
    with pytest.raises(AgentError) as ei:
        load_dataset(spec, roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert [r["index"] for r in codes["TARGET_NOT_BINARY"]["rows"]] == [2, 3]
    p2 = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,1,x\n002,g1,1.5,2.5,b,1,x\n003,g2,2.0,3.0,a,true,x\n004,g3,2.5,3.5,c,TRUE,x\n",
        name="one.csv",
    )
    with pytest.raises(AgentError) as ei2:
        load_dataset(spec.model_copy(update={"data_path": str(p2)}), roots=[tmp_path])
    assert "SINGLE_CLASS" in issue_codes(ei2.value)
    p3 = write_csv(
        tmp_path,
        "001,g1,1.0,2.0,a,1,x\n002,g1,1.5,2.5,b,false,x\n003,g2,2.0,3.0,a,0,x\n004,g3,2.5,3.5,c,True,x\n",
        name="ok.csv",
    )
    df, rep = load_dataset(spec.model_copy(update={"data_path": str(p3)}), roots=[tmp_path])
    assert df["y"].tolist() == [1, 0, 0, 1] and rep.target_stats["positives"] == 2


def test_constant_target_rejected(tmp_path: Path) -> None:
    p = write_csv(tmp_path, "001,g1,1.0,2.0,a,5.0,x\n002,g1,1.5,2.5,b,5.0,x\n003,g2,2.0,3.0,a,5.0,x\n")
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[tmp_path])
    assert "CONSTANT_TARGET" in issue_codes(ei.value)


def test_unused_column_warning_and_report_roundtrip(tmp_path: Path) -> None:
    p = write_csv(tmp_path, GOOD)
    df, rep = load_dataset(make_spec(p, excluded_columns=[]), roots=[tmp_path])
    assert rep.passed
    assert any(i.code == "UNUSED_COLUMN" and i.level == "warning" for i in rep.issues)
    again = validate_strict(DataReport, rep.model_dump(mode="json"))
    assert again == rep
    assert len(df) == 4 and df["post"].tolist() == ["x"] * 4


def test_outside_root_rejected(tmp_path: Path) -> None:
    p = write_csv(tmp_path, GOOD)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p), roots=[other])
    assert ei.value.code == "E_PATH_OUTSIDE_ROOT"


def test_time_column_parse_error(tmp_path: Path) -> None:
    header = "id,grp,x1,x2,c1,y,post,t\n"
    body = "001,g1,1.0,2.0,a,10.0,x,2024-01-01T00:00:00\n002,g1,1.5,2.5,b,11.0,x,not-a-date\n003,g2,2.0,3.0,a,12.5,x,2024-01-03\n"
    p = write_csv(tmp_path, body, header=header)
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(p, split_policy="time", time_column="t"), roots=[tmp_path])
    codes = issue_codes(ei.value)
    assert [r["index"] for r in codes["TIME_UNPARSABLE"]["rows"]] == [1]


def test_tsv_and_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "d.tsv"
    p.write_text(HEADER.replace(",", "\t") + GOOD.replace(",", "\t"), encoding="utf-8")
    df, rep = load_dataset(make_spec(p), roots=[tmp_path])
    assert rep.passed and len(df) == 4
    with pytest.raises(AgentError) as ei:
        load_dataset(make_spec(tmp_path / "none.csv"), roots=[tmp_path])
    assert ei.value.code == "E_INPUT_INVALID"
