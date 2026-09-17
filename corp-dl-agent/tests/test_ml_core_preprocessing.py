"""전처리 시험: train-only fit(validation 통계 무영향), sklearn 일치, unknown category, 고유 ID 과다 열 E_LEAKAGE, dense float32, json+npz 상태 저장."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.preprocessing import (
    JSON_NAME,
    NPZ_NAME,
    FittedPreprocessor,
    build_preprocessor,
    check_leakage,
    fit_preprocessor,
)
from corp_dl_agent.ml.taskspec import taskspec_from_dict


def spec(**overrides: Any) -> Any:
    d: dict[str, Any] = {
        "task_id": "p",
        "task_type": "regression",
        "data_path": "x.csv",
        "target": "y",
        "numeric_features": ["x1", "x2"],
        "categorical_features": ["c1"],
        "id_column": "id",
        "group_column": "grp",
        "metric": "mae",
        "direction": "min",
        "data_origin": "synthetic",
    }
    d.update(overrides)
    return taskspec_from_dict(d)


def frame(n: int, *, offset: float = 0.0, cats: tuple[str, ...] = ("a", "b", "c"), start: int = 0) -> Any:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(start + 1)
    return pd.DataFrame(
        {
            "id": [f"{start + i:05d}" for i in range(n)],
            "grp": [f"g{(start + i) % 4}" for i in range(n)],
            "x1": rng.normal(0.0, 1.0, n) + offset,
            "x2": rng.uniform(-5.0, 5.0, n) + offset,
            "c1": [cats[i % len(cats)] for i in range(n)],
            "y": rng.normal(0.0, 1.0, n),
        }
    )


def test_train_only_fit_validation_stats_do_not_influence() -> None:
    import numpy as np

    train = frame(200)
    val = frame(100, offset=1000.0, start=200)
    fp = fit_preprocessor(spec(), train)
    np.testing.assert_allclose(fp.means, train[["x1", "x2"]].to_numpy().mean(axis=0), rtol=0, atol=1e-12)
    np.testing.assert_allclose(
        fp.scales, train[["x1", "x2"]].to_numpy().std(axis=0, ddof=0), rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        fp.medians, np.median(train[["x1", "x2"]].to_numpy(), axis=0), rtol=0, atol=1e-12
    )
    combined_mean = np.concatenate([train[["x1", "x2"]].to_numpy(), val[["x1", "x2"]].to_numpy()]).mean(
        axis=0
    )
    assert not np.allclose(fp.means, combined_mean)
    Xtr = fp.transform(train)
    assert abs(float(Xtr[:, 0].mean())) < 1e-6 and abs(float(Xtr[:, 0].std()) - 1.0) < 1e-5
    Xva = fp.transform(val)
    assert float(Xva[:, 0].mean()) > 100.0  # validation 은 train 통계로만 변환된다
    fp_again = fit_preprocessor(spec(), train)
    assert fp_again.to_state()[0] == fp.to_state()[0]


def test_matches_sklearn_transform_including_unknown_categories() -> None:
    import numpy as np

    train = frame(120)
    val = frame(40, cats=("a", "b", "zzz"), start=500)  # zzz 는 train 에 없음
    s = spec()
    fp = fit_preprocessor(s, train)
    ct = build_preprocessor(s)
    ct.fit(train[s.features])
    assert list(ct.get_feature_names_out()) == fp.feature_names
    np.testing.assert_allclose(fp.transform(train), ct.transform(train[s.features]), atol=1e-6)
    np.testing.assert_allclose(fp.transform(val), ct.transform(val[s.features]), atol=1e-6)
    X, rep = fp.transform_with_report(val)
    assert X.dtype == np.float32 and X.shape == (40, 2 + 3)
    assert rep.unknown_categories == {"c1": sum(1 for c in val["c1"] if c == "zzz")}
    unknown_rows = X[(val["c1"] == "zzz").to_numpy()][:, 2:]
    assert np.all(unknown_rows == 0.0)  # infrequent 열이 없으면 unknown 은 모두 0


def test_infrequent_categories_with_max_categories() -> None:
    import numpy as np

    n_cat = 70
    cats = tuple(f"k{i:03d}" for i in range(n_cat))
    train = frame(n_cat * 10, cats=cats)
    s = spec()
    fp = fit_preprocessor(s, train, max_categories=64)
    assert fp.infrequent[0] and fp.feature_names[-1] == "cat__c1_infrequent_sklearn"
    assert fp.n_features == 2 + 64
    ct = build_preprocessor(s, max_categories=64)
    ct.fit(train[s.features])
    np.testing.assert_allclose(fp.transform(train), ct.transform(train[s.features]), atol=1e-6)
    val = frame(30, cats=("unknown-cat",), start=9000)
    Xv, rep = fp.transform_with_report(val)
    np.testing.assert_allclose(Xv, ct.transform(val[s.features]), atol=1e-6)
    assert rep.unknown_categories == {"c1": 30}
    assert np.all(Xv[:, -1] == 1.0)  # unknown → infrequent 열


def test_high_cardinality_id_like_column_is_leakage() -> None:
    train = frame(100, cats=tuple(f"u{i}" for i in range(100)))
    findings = check_leakage(train, spec())
    assert findings and findings[0].column == "c1"
    with pytest.raises(AgentError) as ei:
        fit_preprocessor(spec(), train)
    assert ei.value.code == "E_LEAKAGE"
    same_as_id = frame(50)
    same_as_id["c1"] = same_as_id["id"]
    assert any(f.reason.startswith("ID") for f in check_leakage(same_as_id, spec()))
    consecutive = frame(50)
    consecutive["x1"] = [float(i) for i in range(50)]
    with pytest.raises(AgentError) as ei2:
        fit_preprocessor(spec(), consecutive)
    assert ei2.value.code == "E_LEAKAGE"
    assert check_leakage(frame(50), spec()) == []


def test_dense_float32_and_memory_limit() -> None:
    import numpy as np

    train = frame(30)
    fp = fit_preprocessor(spec(), train)
    X = fp.transform(train)
    assert isinstance(X, np.ndarray) and X.dtype == np.float32 and X.flags["C_CONTIGUOUS"]
    assert np.isfinite(X).all()
    assert fp.estimate_dense_bytes(30) == 30 * fp.n_features * 4
    with pytest.raises(AgentError) as ei:
        fp.transform(train, max_dense_bytes=10)
    assert ei.value.code == "E_MEMORY_LIMIT"


def test_missing_values_strict_and_allow_missing() -> None:
    import numpy as np

    train = frame(30)
    fp = fit_preprocessor(spec(), train)
    val = frame(5, start=100)
    val.loc[1, "x1"] = np.nan
    with pytest.raises(AgentError) as ei:
        fp.transform(val)
    assert ei.value.code == "E_INPUT_INVALID" and ei.value.details["rows"] == [1]
    X, rep = fp.transform_with_report(val, allow_missing=True)
    assert rep.imputed_values == {"x1": 1}
    expected = (fp.medians[0] - fp.means[0]) / fp.scales[0]
    assert X[1, 0] == pytest.approx(expected, rel=1e-5)
    val.loc[2, "x2"] = np.inf
    with pytest.raises(AgentError):
        fp.transform(val, allow_missing=True)
    with pytest.raises(AgentError) as ei2:
        fp.transform(val.drop(columns=["c1"]))
    assert ei2.value.details["missing_columns"] == ["c1"]


def test_state_roundtrip_json_npz_without_pickle(tmp_path: Path) -> None:
    import json

    import numpy as np

    train = frame(80)
    val = frame(20, cats=("a", "new"), start=300)
    s = spec()
    fp = fit_preprocessor(s, train)
    d = tmp_path / "전처리 상태"
    paths = fp.save(d)
    assert Path(paths["json"]).name == JSON_NAME and Path(paths["npz"]).name == NPZ_NAME
    meta = json.loads((d / JSON_NAME).read_text(encoding="utf-8"))
    assert meta["categories"] == [["a", "b", "c"]] and meta["synthetic"] is True
    assert meta["feature_names"] == fp.feature_names and meta["taskspec_hash"] == fp.taskspec_hash
    with np.load(d / NPZ_NAME, allow_pickle=False) as z:
        assert set(z.files) == {"medians", "means", "scales"}
    loaded = FittedPreprocessor.load(d)
    np.testing.assert_array_equal(loaded.transform(val), fp.transform(val))
    assert loaded.feature_names == fp.feature_names and loaded.n_train_rows == 80
    meta2, arrays = fp.to_state()
    again = FittedPreprocessor.from_state(meta2, arrays)
    np.testing.assert_array_equal(again.transform(val), fp.transform(val))
    # checksum 변조 → 신뢰 불가
    (d / NPZ_NAME).write_bytes((d / NPZ_NAME).read_bytes() + b"\x00")
    with pytest.raises(AgentError) as ei:
        FittedPreprocessor.load(d)
    assert ei.value.code == "E_ARTIFACT_UNTRUSTED"
    meta2["format"] = "other"
    with pytest.raises(AgentError) as ei2:
        FittedPreprocessor.from_state(meta2, arrays)
    assert ei2.value.code == "E_ARTIFACT_UNTRUSTED"
    with pytest.raises(AgentError):
        FittedPreprocessor.load(tmp_path / "none")


def test_numeric_only_and_categorical_only() -> None:
    import numpy as np

    train = frame(40)
    num_only = fit_preprocessor(spec(categorical_features=[]), train)
    assert num_only.feature_names == ["num__x1", "num__x2"]
    cat_only = fit_preprocessor(spec(numeric_features=[], categorical_features=["c1"]), train)
    assert cat_only.feature_names == ["cat__c1_a", "cat__c1_b", "cat__c1_c"]
    X = cat_only.transform(train)
    assert X.shape == (40, 3) and np.all(X.sum(axis=1) == 1.0)
    with pytest.raises(AgentError):
        fit_preprocessor(spec(), train.iloc[0:0])
    with pytest.raises(AgentError):
        fit_preprocessor(spec(numeric_features=["x1", "x9"]), train)
