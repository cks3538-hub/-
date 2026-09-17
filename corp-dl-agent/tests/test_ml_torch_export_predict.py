"""export 번들 시험: sklearn/MLP 저장·신뢰 로드·예측, 새 프로세스 재로드 일치, synthetic 차단, 외부 pickle 거부, predict_cli."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_ml_torch_common import cap_torch_threads, guarded, make_cfg, prepared_data, small_task

from corp_dl_agent.errors import AgentError
from corp_dl_agent.ml.export import (
    MANIFEST_NAME,
    MODEL_JOBLIB_NAME,
    export_bundle,
    load_bundle,
    predict,
    read_manifest,
)
from corp_dl_agent.ml.mlp import arch_from_candidate, build_mlp
from corp_dl_agent.ml.predict import predict_cli
from corp_dl_agent.ml.registry import demo_candidates

pytestmark = pytest.mark.torch
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _guard() -> Iterator[None]:
    cap_torch_threads()
    with guarded():
        yield


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("exp")
    spec = small_task(tmp, "regression")
    cfg = make_cfg(tmp)
    data, fp, train, val, test = prepared_data(spec, cfg)
    return {
        "tmp": tmp,
        "spec": spec,
        "cfg": cfg,
        "data": data,
        "fp": fp,
        "train": train,
        "val": val,
        "test": test,
    }


def _ranges(spec: Any, train: Any) -> dict[str, dict[str, float]]:
    return {c: {"min": float(train[c].min()), "max": float(train[c].max())} for c in spec.numeric_features}


def _common(spec: Any, fp: Any, train: Any, name: str, *, data_origin: str = "synthetic") -> dict[str, Any]:
    return {
        "preprocessor_state": fp,
        "input_schema": {
            "numeric_features": list(spec.numeric_features),
            "categorical_features": list(spec.categorical_features),
            "feature_names": list(fp.feature_names),
            "n_features": fp.n_features,
        },
        "units": dict(spec.units),
        "train_ranges": _ranges(spec, train),
        "data_origin": data_origin,
        "purpose": spec.purpose,
        "run_id": "run-x",
        "task_id": spec.task_id,
        "task_type": spec.task_type,
        "target": spec.target,
        "id_column": spec.id_column,
        "model_name": name,
    }


def _export_sklearn(prepared: dict[str, Any], out: Path, *, data_origin: str = "synthetic") -> Any:
    from sklearn.linear_model import Ridge

    spec, fp, train, data = prepared["spec"], prepared["fp"], prepared["train"], prepared["data"]
    est = Ridge(alpha=1.0).fit(data.X_train, data.y_train)
    manifest = export_bundle(
        out,
        model_kind="sklearn",
        model=est,
        target_inverse={"kind": "identity", "mean": None, "std": None, "unit": "N"},
        threshold=None,
        **_common(spec, fp, train, "Ridge", data_origin=data_origin),
    )
    return est, manifest


def _export_mlp(prepared: dict[str, Any], out: Path) -> Any:
    import torch

    spec, fp, train, data = prepared["spec"], prepared["fp"], prepared["train"], prepared["data"]
    cand = demo_candidates("regression", 42, 1)[0]
    arch = arch_from_candidate(
        cand, task_type="regression", in_dim=fp.n_features, feature_names=list(fp.feature_names)
    )
    torch.manual_seed(11)
    model = build_mlp(arch)
    manifest = export_bundle(
        out,
        model_kind="mlp",
        model=model,
        arch=arch,
        target_inverse=data.target_inverse(),
        threshold=None,
        **_common(spec, fp, train, cand.candidate_id),
    )
    return model, manifest


def test_sklearn_bundle_roundtrip_and_manifest(prepared: dict[str, Any]) -> None:
    import numpy as np

    out = prepared["tmp"] / "번들 sklearn"
    est, manifest = _export_sklearn(prepared, out)
    assert manifest.created_by == "corp-dl-agent" and manifest.kind == "sklearn" and manifest.synthetic
    assert set(manifest.files) == {"preprocessor.json", "preprocessor.npz", MODEL_JOBLIB_NAME}
    assert manifest.model["estimator_class"] == "Ridge" and manifest.train_ranges["thickness_mm"]["min"] > 0
    assert "합성" in manifest.notices[0]
    bundle = load_bundle(out, allow_synthetic=True)
    test = prepared["test"]
    res = predict(bundle, test)
    assert list(res.columns) == ["id", "prediction", "in_train_range"] and len(res) == len(test)
    assert res["id"].tolist() == test[prepared["spec"].id_column].astype(str).tolist()
    direct = est.predict(prepared["fp"].transform(test))
    np.testing.assert_allclose(res["prediction"].to_numpy(), direct, rtol=0, atol=1e-9)
    assert bool(res["in_train_range"].any())
    disk = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert disk["target_inverse"]["kind"] == "identity" and disk["units"]["insertion_force_n"] == "N"


def test_mlp_bundle_reloads_in_new_process_with_identical_predictions(prepared: dict[str, Any]) -> None:
    import numpy as np

    out = prepared["tmp"] / "번들 mlp"
    _, manifest = _export_mlp(prepared, out)
    assert manifest.kind == "mlp" and {"model.pt", "arch.json"} <= set(manifest.files)
    bundle = load_bundle(out, allow_synthetic=True)
    test = prepared["test"]
    in_proc = predict(bundle, test)
    csv_in = prepared["tmp"] / "test_rows.csv"
    test.to_csv(csv_in, index=False, encoding="utf-8", lineterminator="\n")
    csv_out = prepared["tmp"] / "subprocess_pred.csv"
    script = (
        "import sys, pandas as pd\n"
        "from corp_dl_agent.ml.export import load_bundle, predict\n"
        "b = load_bundle(sys.argv[1], allow_synthetic=True)\n"
        "df = pd.read_csv(sys.argv[2], dtype=str, keep_default_na=False)\n"
        "predict(b, df).to_csv(sys.argv[3], index=False)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", script, str(out), str(csv_in), str(csv_out)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    import pandas as pd

    sub = pd.read_csv(csv_out, dtype={"id": str})
    assert sub["id"].tolist() == in_proc["id"].tolist()
    np.testing.assert_allclose(
        sub["prediction"].to_numpy(), in_proc["prediction"].to_numpy(), rtol=0, atol=1e-6
    )
    # 회귀 역변환: 표준화 공간 출력이 원단위로 돌아왔는지 (train target 범위 근처)
    assert in_proc["prediction"].abs().max() < 10 * float(np.abs(prepared["data"].y_train).max() + 1)


def test_synthetic_bundle_rejected_without_flag(prepared: dict[str, Any]) -> None:
    out = prepared["tmp"] / "synthetic_bundle"
    _export_sklearn(prepared, out)
    with pytest.raises(AgentError) as ei:
        load_bundle(out)
    assert ei.value.code == "E_ARTIFACT_SYNTHETIC"
    assert load_bundle(out, allow_synthetic=True).manifest.synthetic is True
    corp = prepared["tmp"] / "corporate_bundle"
    _export_sklearn(prepared, corp, data_origin="corporate")
    assert load_bundle(corp).manifest.data_origin == "corporate"


def test_untrusted_bundles_rejected(prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import joblib

    out = prepared["tmp"] / "tampered"
    _export_sklearn(prepared, out)
    (out / MODEL_JOBLIB_NAME).write_bytes((out / MODEL_JOBLIB_NAME).read_bytes() + b"x")
    with pytest.raises(AgentError) as ei:
        load_bundle(out, allow_synthetic=True)
    assert ei.value.code == "E_ARTIFACT_UNTRUSTED" and ei.value.details["files"] == {
        MODEL_JOBLIB_NAME: "mismatch"
    }
    # created_by 표시가 바뀐 manifest
    out2 = prepared["tmp"] / "foreign_marker"
    _export_sklearn(prepared, out2)
    m = json.loads((out2 / MANIFEST_NAME).read_text(encoding="utf-8"))
    m["created_by"] = "someone-else"
    (out2 / MANIFEST_NAME).write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(AgentError) as ei2:
        load_bundle(out2, allow_synthetic=True)
    assert ei2.value.code == "E_ARTIFACT_UNTRUSTED"
    # manifest 없이 pickle 만 있는 외부 폴더: joblib.load 가 호출되지 않아야 한다
    foreign = prepared["tmp"] / "foreign_pickle"
    foreign.mkdir()
    joblib.dump({"anything": 1}, foreign / MODEL_JOBLIB_NAME)

    def _boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("joblib.load must not be called for untrusted input")

    monkeypatch.setattr(joblib, "load", _boom)
    with pytest.raises(AgentError) as ei3:
        load_bundle(foreign, allow_synthetic=True)
    assert ei3.value.code == "E_ARTIFACT_UNTRUSTED"
    monkeypatch.undo()
    # 파일 누락
    out3 = prepared["tmp"] / "missing_file"
    _export_sklearn(prepared, out3)
    (out3 / "preprocessor.npz").unlink()
    with pytest.raises(AgentError) as ei4:
        load_bundle(out3, allow_synthetic=True)
    assert ei4.value.code == "E_ARTIFACT_UNTRUSTED" and ei4.value.details["files"] == {
        "preprocessor.npz": "missing"
    }
    with pytest.raises(AgentError):
        read_manifest(prepared["tmp"] / "does-not-exist")


def test_classification_bundle_probability_and_label(tmp_path: Path) -> None:
    from sklearn.linear_model import LogisticRegression

    spec = small_task(tmp_path, "binary_classification")
    data, fp, train, val, test = prepared_data(spec, make_cfg(tmp_path))
    est = LogisticRegression(max_iter=2000).fit(data.X_train, data.y_train.astype("int64"))
    out = tmp_path / "cls_bundle"
    export_bundle(
        out,
        model_kind="sklearn",
        model=est,
        target_inverse={"kind": "identity", "mean": None, "std": None, "unit": ""},
        threshold=0.4,
        **_common(spec, fp, train, "LogisticRegression"),
    )
    with pytest.raises(AgentError):  # 분류는 threshold 필수
        export_bundle(
            tmp_path / "bad",
            model_kind="sklearn",
            model=est,
            target_inverse={"kind": "identity"},
            threshold=None,
            **_common(spec, fp, train, "LogisticRegression"),
        )
    bundle = load_bundle(out, allow_synthetic=True)
    res = predict(bundle, test)
    assert list(res.columns) == ["id", "probability", "label", "prediction", "threshold", "in_train_range"]
    assert ((res["probability"] >= 0.4).astype("int64") == res["label"]).all() and (
        res["threshold"] == 0.4
    ).all()
    assert set(res["label"].unique()) <= {0, 1}


def test_predict_cli_writes_csv_and_sidecar(prepared: dict[str, Any]) -> None:
    tmp = prepared["tmp"]
    cfg = prepared["cfg"]
    data_root = Path(cfg.paths.data_root)
    out = data_root / "models" / "ridge"
    _export_sklearn(prepared, out)
    test = prepared["test"].astype(str)
    test.loc[test.index[0], "thickness_mm"] = ""  # 빈 값 → 중앙값 대치
    csv_in = data_root / "입력 데이터.csv"
    csv_in.parent.mkdir(parents=True, exist_ok=True)
    test.to_csv(csv_in, index=False, encoding="utf-8-sig", lineterminator="\n")
    csv_out = data_root / "outputs" / "performance_predictions.csv"
    with pytest.raises(AgentError) as ei:
        predict_cli(out, csv_in, csv_out, cfg)
    assert ei.value.code == "E_ARTIFACT_SYNTHETIC"
    result = predict_cli(out, csv_in, csv_out, cfg, allow_synthetic=True)
    assert (
        result["n_rows"] == len(test)
        and result["imputed_values"] == {"thickness_mm": 1}
        and result["synthetic"] is True
    )
    assert result["input_encoding"] == "utf-8-sig" and result["value_type"] == "predicted"
    raw = csv_out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"in_train_range" in raw
    side = json.loads(Path(result["sidecar"]).read_text(encoding="utf-8"))
    assert side["manifest_sha256"] == result["manifest_sha256"] and side["model_kind"] == "sklearn"
    import pandas as pd

    df = pd.read_csv(csv_out, dtype={"id": str}, encoding="utf-8-sig")
    assert df["id"].tolist() == test["specimen_id"].astype(str).tolist()  # 선행 0 보존
    bad = test.astype(str)
    bad.loc[bad.index[1], "hole_diameter_mm"] = "abc"
    bad_csv = data_root / "bad.csv"
    bad.to_csv(bad_csv, index=False, encoding="utf-8", lineterminator="\n")
    with pytest.raises(AgentError) as ei2:
        predict_cli(out, bad_csv, csv_out, cfg, allow_synthetic=True)
    assert ei2.value.code == "E_INPUT_INVALID" and ei2.value.details["lines"] == [3]
    outside = tmp.parent / "outside-model"
    with pytest.raises(AgentError) as ei3:
        predict_cli(outside, csv_in, csv_out, cfg, allow_synthetic=True)
    assert ei3.value.code == "E_PATH_OUTSIDE_ROOT"
