"""모델 export 번들 (sklearn 기준 모델 / MLP) 과 신뢰 로드·예측.

번들 폴더:
  manifest.json        format/created_by 표시, kind, input_schema, units, target 역변환, threshold, 학습 범위(min/max),
                       data_origin/synthetic/purpose, run_id, 파일별 sha256 (checksums)
  preprocessor.json/.npz   FittedPreprocessor 상태 (pickle 없음)
  model.pt + arch.json     MLP: state_dict (weights_only 로드) + 구조
  model.joblib             sklearn: joblib 직렬화 — manifest 의 checksum 과 created_by 표시가 검증된 뒤에만 로드한다

규칙:
- load_bundle 은 manifest 가 이 프로그램의 것(format/created_by) 이고 모든 파일의 sha256 이 일치할 때만 로드한다.
  외부에서 가져온 임의 pickle/joblib 은 E_ARTIFACT_UNTRUSTED. weights_only=True 만으로 완전 안전을 주장하지 않는다.
- synthetic 번들은 allow_synthetic=False 이면 E_ARTIFACT_SYNTHETIC (운영 자동 채택 금지).
- predict(bundle, df) -> DataFrame(id, prediction[, probability, label], in_train_range). 예측은 detach().cpu().
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, ValidationError, field_validator

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_bytes,
    atomic_write_json,
    now_iso,
    read_json,
    sha256_file,
    validate_strict,
)
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.baselines import CLASSIFICATION_BASELINES, REGRESSION_BASELINES, predict_baseline
from corp_dl_agent.ml.mlp import MlpArch, build_mlp, torch_module
from corp_dl_agent.ml.preprocessing import JSON_NAME, NPZ_NAME, FittedPreprocessor
from corp_dl_agent.version import SCHEMA_VERSION, __version__

if TYPE_CHECKING:
    import pandas as pd

BUNDLE_FORMAT = "corp-dl-agent/model-bundle/1"
CREATED_BY = "corp-dl-agent"
MANIFEST_NAME = "manifest.json"
MODEL_PT_NAME = "model.pt"
ARCH_NAME = "arch.json"
MODEL_JOBLIB_NAME = "model.joblib"
ALLOWED_SKLEARN_CLASSES: frozenset[str] = frozenset(REGRESSION_BASELINES) | frozenset(
    CLASSIFICATION_BASELINES
)
SYNTHETIC_NOTICE = "합성(synthetic) 데이터로 학습한 모델입니다. 사내 운영에서 자동 선택되지 않으며 업무 판단에 사용하지 마세요."
ModelKind = Literal["sklearn", "mlp"]


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "모델 export/predict") from exc
    return np


def _pd() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("pandas", "모델 predict") from exc
    return pd


def _joblib() -> Any:
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("joblib", "sklearn 모델 export/predict") from exc
    return joblib


class TargetInverse(StrictModel):
    kind: Literal["identity", "standardize"] = "identity"
    mean: float | None = None
    std: float | None = None
    unit: str = ""

    def apply(self, arr: Any) -> Any:
        np = _np()
        a = np.asarray(arr, dtype="float64").reshape(-1)
        if self.kind == "standardize":
            return a * float(self.std or 1.0) + float(self.mean or 0.0)
        return a


class BundleManifest(StrictModel):
    format: str = BUNDLE_FORMAT
    created_by: str = CREATED_BY
    app_version: str = __version__
    schema_version: str = SCHEMA_VERSION
    created_at: str
    kind: ModelKind
    model_name: str
    run_id: str
    task_id: str
    task_type: Literal["regression", "binary_classification"]
    target: str
    id_column: str
    input_schema: dict[str, Any]
    units: dict[str, str] = Field(default_factory=dict)
    target_inverse: TargetInverse
    threshold: float | None = None
    train_ranges: dict[str, dict[str, float]] = Field(default_factory=dict)
    data_origin: Literal["synthetic", "corporate"]
    synthetic: bool
    purpose: str = ""
    model: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    notices: list[str] = Field(default_factory=list)
    hashes: dict[str, str] = Field(default_factory=dict)

    @field_validator("format")
    @classmethod
    def _format(cls, v: str) -> str:
        if v != BUNDLE_FORMAT:
            raise ValueError(f"번들 format 이 이 프로그램의 것이 아닙니다: {v}")
        return v

    @field_validator("created_by")
    @classmethod
    def _created_by(cls, v: str) -> str:
        if v != CREATED_BY:
            raise ValueError(f"created_by 표시가 없습니다: {v}")
        return v


@dataclass
class LoadedBundle:
    directory: Path
    manifest: BundleManifest
    preprocessor: FittedPreprocessor
    model: Any
    arch: MlpArch | None = None
    manifest_sha256: str = ""


def _untrusted(msg: str, **details: Any) -> AgentError:
    return AgentError("E_ARTIFACT_UNTRUSTED", msg, details=details)


# ---------------------------------------------------------------------------- export
def export_bundle(
    directory: str | os.PathLike[str],
    *,
    model_kind: ModelKind,
    model: Any,
    preprocessor_state: FittedPreprocessor,
    input_schema: dict[str, Any],
    units: dict[str, str],
    target_inverse: dict[str, Any] | TargetInverse,
    threshold: float | None,
    train_ranges: dict[str, dict[str, float]],
    data_origin: str,
    purpose: str,
    run_id: str,
    task_id: str,
    task_type: str,
    target: str,
    id_column: str,
    model_name: str,
    arch: MlpArch | None = None,
    notices: list[str] | None = None,
    hashes: dict[str, str] | None = None,
) -> BundleManifest:
    """번들 폴더를 만든다. 반환: manifest (파일별 sha256 포함). 기존 파일은 덮어쓴다."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    if data_origin not in ("synthetic", "corporate"):
        raise AgentError("E_SCHEMA_INVALID", f"data_origin 은 synthetic|corporate 여야 합니다: {data_origin}")
    if task_type not in ("regression", "binary_classification"):
        raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")
    if task_type == "binary_classification" and threshold is None:
        raise AgentError("E_SCHEMA_INVALID", "분류 번들에는 validation 에서 고른 threshold 가 필요합니다")
    if threshold is not None and not (math.isfinite(threshold) and 0.0 <= threshold <= 1.0):
        raise AgentError("E_SCHEMA_INVALID", f"threshold 는 [0,1] 범위여야 합니다: {threshold}")
    ti = (
        target_inverse
        if isinstance(target_inverse, TargetInverse)
        else validate_strict(TargetInverse, target_inverse)
    )
    files: dict[str, str] = {}
    saved = preprocessor_state.save(d)
    for p in saved.values():
        files[Path(p).name] = sha256_file(p)
    model_info: dict[str, Any]
    if model_kind == "mlp":
        torch = torch_module("MLP export")
        if arch is None:
            raise AgentError("E_SCHEMA_INVALID", "MLP 번들에는 arch(MlpArch) 가 필요합니다")
        if arch.in_dim != preprocessor_state.n_features:
            raise AgentError(
                "E_SCHEMA_INVALID",
                f"arch.in_dim({arch.in_dim}) 이 전처리 feature 수({preprocessor_state.n_features}) 와 다릅니다",
            )
        state = model.state_dict() if hasattr(model, "state_dict") else model
        if not isinstance(state, dict) or not state:
            raise AgentError("E_SCHEMA_INVALID", "MLP state_dict 가 비어 있습니다")
        import io

        buf = io.BytesIO()
        torch.save({k: v.detach().cpu() for k, v in state.items()}, buf)
        atomic_write_bytes(d / MODEL_PT_NAME, buf.getvalue())
        atomic_write_json(d / ARCH_NAME, arch.model_dump(mode="json"))
        files[MODEL_PT_NAME] = sha256_file(d / MODEL_PT_NAME)
        files[ARCH_NAME] = sha256_file(d / ARCH_NAME)
        model_info = {
            "arch": arch.model_dump(mode="json"),
            "state_dict_keys": sorted(str(k) for k in state.keys()),
            "torch_version": str(torch.__version__),
            "load": "torch.load(weights_only=True) + strict state_dict",
        }
    elif model_kind == "sklearn":
        joblib = _joblib()
        cls_name = type(model).__name__
        if cls_name not in ALLOWED_SKLEARN_CLASSES:
            raise AgentError(
                "E_SCHEMA_INVALID",
                f"허용된 기준 모델 클래스가 아닙니다: {cls_name}",
                details={"allowed": sorted(ALLOWED_SKLEARN_CLASSES)},
            )
        import io

        buf = io.BytesIO()
        joblib.dump(model, buf)
        atomic_write_bytes(d / MODEL_JOBLIB_NAME, buf.getvalue())
        files[MODEL_JOBLIB_NAME] = sha256_file(d / MODEL_JOBLIB_NAME)
        params = {}
        for k, v in model.get_params(deep=False).items():
            params[k] = v if isinstance(v, (int, float, str, bool)) or v is None else repr(v)
        try:
            import sklearn

            skl_version = str(sklearn.__version__)
        except ImportError:  # pragma: no cover
            skl_version = "unknown"
        model_info = {
            "estimator_class": cls_name,
            "params": params,
            "sklearn_version": skl_version,
            "n_features_in": int(getattr(model, "n_features_in_", preprocessor_state.n_features)),
            "load": "joblib.load (manifest checksum + created_by 검증 뒤에만)",
        }
    else:
        raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 model_kind: {model_kind}")
    notes = list(notices or [])
    if data_origin == "synthetic" and SYNTHETIC_NOTICE not in notes:
        notes.append(SYNTHETIC_NOTICE)
    manifest = BundleManifest(
        created_at=now_iso(),
        kind=model_kind,
        model_name=model_name,
        run_id=run_id,
        task_id=task_id,
        task_type=task_type,  # type: ignore[arg-type]
        target=target,
        id_column=id_column,
        input_schema=dict(input_schema),
        units=dict(units),
        target_inverse=ti,
        threshold=None if threshold is None else float(threshold),
        train_ranges={k: {"min": float(v["min"]), "max": float(v["max"])} for k, v in train_ranges.items()},
        data_origin=data_origin,  # type: ignore[arg-type]
        synthetic=data_origin == "synthetic",
        purpose=purpose,
        model=model_info,
        files=files,
        notices=notes,
        hashes=dict(hashes or {}),
    )
    atomic_write_json(d / MANIFEST_NAME, manifest.model_dump(mode="json"))
    return manifest


# ---------------------------------------------------------------------------- load
def read_manifest(directory: str | os.PathLike[str]) -> BundleManifest:
    d = Path(directory)
    mp = d / MANIFEST_NAME
    if not d.is_dir() or not mp.is_file():
        raise _untrusted("manifest.json 이 없어 이 프로그램이 만든 번들로 볼 수 없습니다", dir=str(d))
    try:
        data = read_json(mp)
        manifest: BundleManifest = validate_strict(BundleManifest, data)
    except (ValueError, ValidationError, OSError) as exc:
        raise _untrusted(
            "manifest.json 이 이 프로그램의 번들 형식과 다릅니다", dir=str(d), error=str(exc)[:300]
        ) from exc
    return manifest


def verify_bundle_files(directory: Path, manifest: BundleManifest) -> dict[str, str]:
    """manifest.files 의 각 파일 sha256 검증. 결과 {파일: ok|missing|mismatch|outside}. 하나라도 ok 가 아니면 예외."""
    base = directory.resolve()
    result: dict[str, str] = {}
    for rel, expected in manifest.files.items():
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            result[rel] = "outside"
            continue
        if not target.is_file():
            result[rel] = "missing"
            continue
        result[rel] = "ok" if sha256_file(target) == expected else "mismatch"
    bad = {k: v for k, v in result.items() if v != "ok"}
    if bad:
        raise _untrusted("번들 파일의 checksum 검증에 실패했습니다", dir=str(directory), files=bad)
    required = {JSON_NAME, NPZ_NAME} | (
        {MODEL_PT_NAME, ARCH_NAME} if manifest.kind == "mlp" else {MODEL_JOBLIB_NAME}
    )
    missing = sorted(required - set(manifest.files))
    if missing:
        raise _untrusted("manifest 에 필수 파일 checksum 이 없습니다", dir=str(directory), missing=missing)
    return result


def load_bundle(directory: str | os.PathLike[str], *, allow_synthetic: bool = False) -> LoadedBundle:
    """checksum 일치·created_by 표시가 확인된 번들만 로드한다. synthetic 은 allow_synthetic=True 일 때만."""
    d = Path(directory)
    manifest = read_manifest(d)
    verify_bundle_files(d, manifest)
    if manifest.synthetic and not allow_synthetic:
        raise AgentError(
            "E_ARTIFACT_SYNTHETIC",
            f"합성 데이터로 학습한 모델입니다 ({manifest.model_name}). 운영 자동 채택이 차단되었습니다.",
            details={"dir": str(d), "data_origin": manifest.data_origin, "run_id": manifest.run_id},
        )
    preprocessor = FittedPreprocessor.load(d)
    n_features = int(manifest.input_schema.get("n_features", preprocessor.n_features))
    if preprocessor.n_features != n_features:
        raise _untrusted("전처리 feature 수가 manifest 와 다릅니다", dir=str(d))
    arch: MlpArch | None = None
    model: Any
    if manifest.kind == "mlp":
        torch = torch_module("MLP predict")
        try:
            arch = validate_strict(MlpArch, read_json(d / ARCH_NAME))
        except (ValueError, ValidationError, OSError) as exc:
            raise _untrusted("arch.json 검증 실패", dir=str(d), error=str(exc)[:200]) from exc
        if arch.in_dim != preprocessor.n_features:
            raise _untrusted("arch.in_dim 이 전처리 feature 수와 다릅니다", dir=str(d))
        try:
            state = torch.load(d / MODEL_PT_NAME, map_location="cpu", weights_only=True)
        except Exception as exc:  # noqa: BLE001 - torch.load 예외 종류가 다양함
            raise _untrusted("model.pt 를 읽을 수 없습니다", dir=str(d), error=str(exc)[:200]) from exc
        if not isinstance(state, dict) or not all(hasattr(v, "shape") for v in state.values()):
            raise _untrusted("model.pt 는 텐서 state_dict 여야 합니다", dir=str(d))
        expected_keys = manifest.model.get("state_dict_keys")
        if isinstance(expected_keys, list) and sorted(str(k) for k in state.keys()) != sorted(expected_keys):
            raise _untrusted("state_dict 키가 manifest 와 다릅니다", dir=str(d))
        model = build_mlp(arch)
        try:
            model.load_state_dict(state, strict=True)
        except RuntimeError as exc:
            raise _untrusted(
                "state_dict 가 모델 구조와 맞지 않습니다", dir=str(d), error=str(exc)[:200]
            ) from exc
        model.eval()
    else:
        joblib = _joblib()
        expected_cls = str(manifest.model.get("estimator_class", ""))
        if expected_cls not in ALLOWED_SKLEARN_CLASSES:
            raise _untrusted(
                "manifest 의 estimator_class 가 허용 목록에 없습니다", dir=str(d), estimator=expected_cls
            )
        try:
            model = joblib.load(d / MODEL_JOBLIB_NAME)  # checksum + created_by 검증 뒤에만 도달한다
        except Exception as exc:  # noqa: BLE001
            raise _untrusted("model.joblib 를 읽을 수 없습니다", dir=str(d), error=str(exc)[:200]) from exc
        if type(model).__name__ != expected_cls:
            raise _untrusted(
                "로드한 estimator 클래스가 manifest 와 다릅니다",
                dir=str(d),
                expected=expected_cls,
                actual=type(model).__name__,
            )
        n_in = getattr(model, "n_features_in_", None)
        if n_in is not None and int(n_in) != preprocessor.n_features:
            raise _untrusted("estimator 의 입력 feature 수가 전처리와 다릅니다", dir=str(d))
    return LoadedBundle(
        directory=d,
        manifest=manifest,
        preprocessor=preprocessor,
        model=model,
        arch=arch,
        manifest_sha256=sha256_file(d / MANIFEST_NAME),
    )


# ---------------------------------------------------------------------------- predict
def _in_train_range(df: Any, manifest: BundleManifest) -> Any:
    np = _np()
    n = int(len(df))
    ok = np.ones(n, dtype=bool)
    for col, rng in manifest.train_ranges.items():
        if col not in df.columns:
            continue
        vals = np.asarray(_pd().to_numeric(df[col], errors="coerce"), dtype="float64")
        inside = np.isfinite(vals) & (vals >= float(rng["min"])) & (vals <= float(rng["max"]))
        ok &= inside
    return ok


def raw_predictions(bundle: LoadedBundle, X: Any, *, batch_size: int = 1024) -> Any:
    """전처리된 X → 회귀 원단위 예측 / 분류 양성 확률 (float64, (N,))."""
    np = _np()
    if bundle.manifest.kind == "mlp":
        from corp_dl_agent.ml.trainer import predict_in_batches

        raw = predict_in_batches(bundle.model, X, task_type=bundle.manifest.task_type, batch_size=batch_size)
        if bundle.manifest.task_type == "regression":
            return bundle.manifest.target_inverse.apply(raw)
        return np.clip(np.asarray(raw, dtype="float64"), 0.0, 1.0)
    pred = predict_baseline(bundle.model, X, bundle.manifest.task_type)
    if bundle.manifest.task_type == "regression":
        return bundle.manifest.target_inverse.apply(pred)
    return np.clip(np.asarray(pred, dtype="float64"), 0.0, 1.0)


def predict(
    bundle: LoadedBundle, df: pd.DataFrame, *, batch_size: int = 1024, allow_missing: bool = True
) -> pd.DataFrame:
    """id / prediction / (probability, label) / in_train_range. 입력 열 누락은 E_INPUT_INVALID."""
    np = _np()
    pd = _pd()
    m = bundle.manifest
    if len(df) == 0:
        raise AgentError("E_INPUT_INVALID", "예측할 행이 없습니다")
    ids = (
        df[m.id_column].astype(str).tolist()
        if m.id_column in df.columns
        else [f"row-{i + 1:06d}" for i in range(len(df))]
    )
    X = bundle.preprocessor.transform(df, allow_missing=allow_missing)
    values = raw_predictions(bundle, X, batch_size=batch_size)
    if not np.isfinite(values).all():
        raise AgentError("E_INTERNAL", "예측 결과에 NaN/Inf 가 있습니다")
    out = pd.DataFrame({"id": ids})
    if m.task_type == "regression":
        out["prediction"] = values
    else:
        thr = float(m.threshold) if m.threshold is not None else 0.5
        out["probability"] = values
        out["label"] = (values >= thr).astype("int64")
        out["prediction"] = out["label"]
        out["threshold"] = thr
    out["in_train_range"] = _in_train_range(df, m)
    return out
