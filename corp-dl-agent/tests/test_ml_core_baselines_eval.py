"""기준 모델·평가·registry·합성 생성기 시험."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_dl_agent.common import sha256_file
from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.baselines import (
    CLASSIFICATION_BASELINES,
    REGRESSION_BASELINES,
    baseline_models,
    baseline_names,
    baseline_params,
    fit_baseline,
    predict_baseline,
)
from corp_dl_agent.ml.data import load_dataset
from corp_dl_agent.ml.evaluation import (
    choose_threshold,
    evaluate_classification,
    evaluate_regression,
    is_better,
    is_finite_metrics,
    select_best,
)
from corp_dl_agent.ml.preprocessing import fit_preprocessor
from corp_dl_agent.ml.registry import MLP_SEARCH_SPACE, MlpCandidate, demo_candidates, validate_candidate
from corp_dl_agent.ml.split import apply_split, group_split
from corp_dl_agent.ml.synthetic import (
    CLASSIFICATION_TARGET,
    DISCLAIMER_KO,
    NUMERIC_FEATURES,
    POST_COLUMN,
    REGRESSION_TARGET,
    generate_clip_dataset,
    write_fixtures,
)
from corp_dl_agent.ml.taskspec import load_taskspec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"


def prepared(name: str):  # type: ignore[no-untyped-def]
    spec = load_taskspec(FIXTURES / name)
    df, rep = load_dataset(spec, roots=[FIXTURES])
    man = group_split(df, spec, code_hash="0" * 64, data_hash=rep.sha256)
    tr, va, te = apply_split(df, man)
    fp = fit_preprocessor(spec, tr)
    return spec, fp, (tr, va, te)


def test_baseline_registry_names() -> None:
    assert tuple(baseline_models("regression")) == REGRESSION_BASELINES
    assert tuple(baseline_models("binary_classification")) == CLASSIFICATION_BASELINES
    assert baseline_names("regression") == REGRESSION_BASELINES
    with pytest.raises(AgentError):
        baseline_models("multiclass")
    with pytest.raises(AgentError):
        baseline_names("x")


def test_regression_baselines_fit_predict_and_metrics() -> None:
    import numpy as np

    spec, fp, (tr, va, te) = prepared("task_regression.yaml")
    Xtr, Xva = fp.transform(tr), fp.transform(va)
    ytr, yva = tr[spec.target].to_numpy(), va[spec.target].to_numpy()
    results = {}
    for name, est in baseline_models("regression", seed=spec.seed).items():
        fit_baseline(name, est, Xtr, ytr)
        pred = predict_baseline(est, Xva, "regression")
        assert pred.shape == (len(va),) and pred.dtype == np.float64
        m = evaluate_regression(yva, pred, spec.target_unit())
        assert is_finite_metrics(m) and m["unit"] == "N" and m["n"] == len(va)
        results[name] = m
        assert isinstance(baseline_params(est), dict)
    assert results["Ridge"]["mae"] < results["DummyRegressor"]["mae"]
    assert results["HistGradientBoostingRegressor"]["r2"] > 0.5
    best, val = select_best(results, "mae", "min")
    assert best in ("Ridge", "HistGradientBoostingRegressor") and val == results[best]["mae"]
    assert select_best(results, "r2", "max")[0] != "DummyRegressor"
    # 재현성: 같은 seed 로 다시 학습하면 같은 예측
    est2 = baseline_models("regression", seed=spec.seed)["HistGradientBoostingRegressor"]
    fit_baseline("HistGradientBoostingRegressor", est2, Xtr, ytr)
    est1 = baseline_models("regression", seed=spec.seed)["HistGradientBoostingRegressor"]
    fit_baseline("HistGradientBoostingRegressor", est1, Xtr, ytr)
    np.testing.assert_allclose(
        predict_baseline(est1, Xva, "regression"), predict_baseline(est2, Xva, "regression")
    )


def test_classification_baselines_threshold_from_validation_only() -> None:
    import numpy as np

    spec, fp, (tr, va, te) = prepared("task_classification.yaml")
    Xtr, Xva, Xte = fp.transform(tr), fp.transform(va), fp.transform(te)
    ytr, yva, yte = (d[spec.target].to_numpy() for d in (tr, va, te))
    for name, est in baseline_models("binary_classification", seed=spec.seed).items():
        fit_baseline(name, est, Xtr, ytr)
        p_val = predict_baseline(est, Xva, "binary_classification")
        assert p_val.shape == (len(va),) and np.all((p_val >= 0) & (p_val <= 1))
        thr = choose_threshold(yva, p_val, metric="f1")
        assert 0.0 <= thr <= 1.0 and thr in set(p_val.tolist())
        m_val = evaluate_classification(yva, p_val, thr)
        m_half = evaluate_classification(yva, p_val, 0.5)
        assert m_val["f1"] >= m_half["f1"] - 1e-12  # validation 에서 고른 임계값은 0.5 보다 나쁘지 않다
        cm = m_val["confusion_matrix"]
        assert cm["tn"] + cm["fp"] + cm["fn"] + cm["tp"] == len(va)
        # test 는 고정된 임계값으로 한 번만 평가한다
        m_test = evaluate_classification(yte, predict_baseline(est, Xte, "binary_classification"), thr)
        assert m_test["threshold"] == thr and is_finite_metrics(m_test)
        if name != "DummyClassifier":
            assert m_test["roc_auc"] > 0.8 and m_test["average_precision"] > 0.8
    dummy = baseline_models("binary_classification")["DummyClassifier"]
    fit_baseline("DummyClassifier", dummy, Xtr, ytr)
    assert (
        evaluate_classification(yva, predict_baseline(dummy, Xva, "binary_classification"), 0.5)["roc_auc"]
        == 0.5
    )


def test_evaluate_regression_manual_values() -> None:
    import numpy as np

    y = np.array([1.0, 2.0, 3.0, 4.0])
    p = np.array([1.0, 2.0, 3.0, 8.0])
    m = evaluate_regression(y, p, "kg")
    assert m["mae"] == pytest.approx(1.0) and m["rmse"] == pytest.approx(2.0)
    assert m["p95_abs_error"] == pytest.approx(float(np.percentile(np.abs(y - p), 95)))
    assert m["max_abs_error"] == 4.0 and m["unit"] == "kg"
    assert m["r2"] == pytest.approx(1 - 16.0 / 5.0)
    const = evaluate_regression(np.ones(4), np.ones(4), "")
    assert const["r2"] != const["r2"] and not is_finite_metrics(const)  # NaN 은 숨기지 않는다
    with pytest.raises(AgentError):
        evaluate_regression([1.0, 2.0], [1.0], "")
    with pytest.raises(AgentError):
        evaluate_regression([1.0, float("nan")], [1.0, 1.0], "")


def test_evaluate_classification_manual_values() -> None:
    y = [1, 1, 0, 0, 1, 0]
    p = [0.9, 0.4, 0.6, 0.2, 0.8, 0.1]
    m = evaluate_classification(y, p, 0.5)
    assert m["confusion_matrix"] == {"tn": 2, "fp": 1, "fn": 1, "tp": 2}
    assert (
        m["precision"] == pytest.approx(2 / 3)
        and m["recall"] == pytest.approx(2 / 3)
        and m["f1"] == pytest.approx(2 / 3)
    )
    assert m["definitions"]["positive_rule"] == "predicted positive := probability >= threshold"
    assert 0.0 < m["roc_auc"] < 1.0 and 0.0 < m["average_precision"] <= 1.0
    thr = choose_threshold(y, p)
    assert thr == pytest.approx(0.4)  # p>=0.4 → tp=3, fp=1, fn=0 → f1=6/7 (최대)
    assert evaluate_classification(y, p, thr)["f1"] == pytest.approx(6 / 7)
    with pytest.raises(AgentError):
        evaluate_classification([1, 2, 0], [0.1, 0.2, 0.3], 0.5)
    with pytest.raises(AgentError):
        evaluate_classification(y, p, 1.5)
    with pytest.raises(AgentError):
        choose_threshold([1, 1, 1], [0.1, 0.2, 0.3])
    with pytest.raises(AgentError):
        choose_threshold(y, p, metric="accuracy")
    ba = choose_threshold(y, p, metric="balanced_accuracy")
    assert 0.0 <= ba <= 1.0
    single = evaluate_classification([1, 1], [0.2, 0.9], 0.5)
    assert single["roc_auc"] != single["roc_auc"]  # 한 클래스 → NaN, 숨기지 않음


def test_is_finite_and_is_better_and_select_best() -> None:
    assert is_finite_metrics({"a": 1, "b": {"c": [1.0, 2]}, "unit": "N", "ok": True})
    assert not is_finite_metrics({"a": float("inf")})
    assert not is_finite_metrics({"a": None})
    assert is_better(1.0, 2.0, "min") and not is_better(2.0, 1.0, "min") and is_better(2.0, 1.0, "max")
    assert is_better(1.0, None, "min") and not is_better(float("nan"), 1.0, "min")
    res = {"B": {"mae": 1.0}, "A": {"mae": 1.0}, "C": {"mae": float("nan")}}
    assert select_best(res, "mae", "min") == ("A", 1.0)
    with pytest.raises(AgentError):
        select_best({"C": {"mae": float("nan")}}, "mae", "min")
    with pytest.raises(AgentError):
        select_best(res, "mae", "sideways")


def test_registry_validate_and_demo_candidates() -> None:
    c = validate_candidate(
        {"hidden_dims": [64, 32], "dropout": 0.1, "learning_rate": 1e-3, "weight_decay": 0.0}
    )
    assert isinstance(c, MlpCandidate) and c.candidate_id.startswith("mlp-") and c.model_kind == "mlp"
    assert (
        c.config_hash()
        == validate_candidate(
            {
                "hidden_dims": [64, 32],
                "dropout": 0.1,
                "learning_rate": 1e-3,
                "weight_decay": 0.0,
                "candidate_id": "x1",
            }
        ).config_hash()
    )
    for bad in (
        {"hidden_dims": [256, 128], "dropout": 0.1, "learning_rate": 1e-3, "weight_decay": 0.0},
        {"hidden_dims": [64, 32], "dropout": 0.5, "learning_rate": 1e-3, "weight_decay": 0.0},
        {"hidden_dims": [64, 32], "dropout": 0.1, "learning_rate": 1e-2, "weight_decay": 0.0},
        {"hidden_dims": [64, 32], "dropout": 0.1, "learning_rate": 1e-3, "weight_decay": 0.01},
        {
            "hidden_dims": [64, 32],
            "dropout": 0.1,
            "learning_rate": 1e-3,
            "weight_decay": 0.0,
            "batch_size": 7,
        },
        {"hidden_dims": [64, 32], "dropout": 0.1, "learning_rate": 1e-3, "weight_decay": 0.0, "extra": 1},
        {"hidden_dims": [64, 32], "dropout": "0.1", "learning_rate": 1e-3, "weight_decay": 0.0},
        {
            "hidden_dims": [64, 32],
            "dropout": 0.1,
            "learning_rate": 1e-3,
            "weight_decay": 0.0,
            "activation": "gelu",
        },
    ):
        with pytest.raises(AgentError) as ei:
            validate_candidate(bad)
        assert ei.value.code == "E_SCHEMA_INVALID"
    a = demo_candidates("regression", 42, 4)
    b = demo_candidates("regression", 42, 4)
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    assert len({x.config_hash() for x in a}) == 4 and len({x.candidate_id for x in a}) == 4
    for x in a:
        assert x.hidden_dims in MLP_SEARCH_SPACE["hidden_dims"]
        assert MLP_SEARCH_SPACE["dropout"][0] <= x.dropout <= MLP_SEARCH_SPACE["dropout"][1]
        assert MLP_SEARCH_SPACE["learning_rate"][0] <= x.learning_rate <= MLP_SEARCH_SPACE["learning_rate"][1]
    assert demo_candidates("binary_classification", 42, 2)[1].model_dump() != a[1].model_dump()
    with pytest.raises(AgentError):
        demo_candidates("regression", 42, 0)
    with pytest.raises(AgentError):
        demo_candidates("other", 42, 1)


def test_synthetic_generator_deterministic_and_documented(tmp_path: Path) -> None:
    a = generate_clip_dataset(120, 8, 5, "regression")
    b = generate_clip_dataset(120, 8, 5, "regression")
    assert a.equals(b)
    assert not a.equals(generate_clip_dataset(120, 8, 6, "regression"))
    assert REGRESSION_TARGET in a.columns and POST_COLUMN in a.columns and a["group_id"].nunique() == 8
    assert a["specimen_id"].str.len().eq(6).all() and a["specimen_id"].iloc[0] == "000001"
    c = generate_clip_dataset(120, 8, 5, "binary_classification")
    assert set(c[CLASSIFICATION_TARGET].unique()) == {0, 1}
    for col in NUMERIC_FEATURES:
        assert a[col].notna().all()
    with pytest.raises(AgentError):
        generate_clip_dataset(5, 10, 1, "regression")
    out = write_fixtures(tmp_path / "ml")
    assert sorted(out) == [
        "README.md",
        "clip_classification.csv",
        "clip_regression.csv",
        "task_classification.yaml",
        "task_regression.yaml",
    ]
    readme = (tmp_path / "ml" / "README.md").read_text(encoding="utf-8")
    assert "실제 차량 부품·재료 물리를 대표하지 않습니다" in readme and DISCLAIMER_KO in readme
    assert "synthetic" in readme
    # 저장소 fixture 는 생성기와 일치해야 한다 (변경 시 재생성 필요)
    for name in (
        "clip_regression.csv",
        "clip_classification.csv",
        "task_regression.yaml",
        "task_classification.yaml",
        "README.md",
    ):
        assert sha256_file(out[name]) == sha256_file(FIXTURES / name), name
