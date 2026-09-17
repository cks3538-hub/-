"""전처리: train 에서만 fit 하는 ColumnTransformer 와 pickle 없이 저장/복원되는 FittedPreprocessor.

- numeric: SimpleImputer(median) + StandardScaler ; categorical: OneHotEncoder(handle_unknown="infrequent_if_exist", max_categories)
- fit 은 train 만 받는다 (fit_preprocessor(spec, train_df)). validation/test 통계는 절대 fit 에 들어가지 않는다.
- 고유 ID 성격의 열(고유값 비율 과다, ID 열과 동일)은 E_LEAKAGE.
- transform 출력은 dense float32. 크기가 상한을 넘으면 E_MEMORY_LIMIT.
- 상태는 preprocessor.json (열/카테고리/feature 이름/checksum) + preprocessor.npz (median/mean/scale) 로 저장한다.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_bytes,
    atomic_write_json,
    now_iso,
    read_json,
    sha256_bytes,
)
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.taskspec import TaskSpec, taskspec_hash

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

STATE_FORMAT = "corp-dl-agent/preprocessor/1"
JSON_NAME = "preprocessor.json"
NPZ_NAME = "preprocessor.npz"
MAX_CATEGORIES = 64
LEAKAGE_MIN_UNIQUE = 20
LEAKAGE_UNIQUE_RATIO = 0.5
DEFAULT_MAX_DENSE_BYTES = 4 * 1024**3
INFREQUENT_NAME = "infrequent_sklearn"


class LeakageFinding(StrictModel):
    column: str
    reason: str
    n_unique: int
    n_rows: int


class TransformReport(StrictModel):
    n_rows: int
    n_features: int
    unknown_categories: dict[str, int] = Field(default_factory=dict)
    imputed_values: dict[str, int] = Field(default_factory=dict)
    dense_bytes: int


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "전처리") from exc
    return np


def _sklearn_parts() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("scikit-learn", "전처리") from exc
    return ColumnTransformer, SimpleImputer, Pipeline, OneHotEncoder, StandardScaler


def build_preprocessor(spec: TaskSpec, *, max_categories: int = MAX_CATEGORIES) -> Any:
    """미학습 sklearn ColumnTransformer. fit 은 반드시 train 데이터로만 한다."""
    np = _np()
    ColumnTransformer, SimpleImputer, Pipeline, OneHotEncoder, StandardScaler = _sklearn_parts()
    transformers: list[tuple[str, Any, list[str]]] = []
    if spec.numeric_features:
        transformers.append(
            (
                "num",
                Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
                list(spec.numeric_features),
            )
        )
    if spec.categorical_features:
        transformers.append(
            (
                "cat",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    max_categories=max_categories,
                    sparse_output=False,
                    dtype=np.float32,
                ),
                list(spec.categorical_features),
            )
        )
    return ColumnTransformer(
        transformers, remainder="drop", sparse_threshold=0.0, verbose_feature_names_out=True
    )


def check_leakage(df: pd.DataFrame, spec: TaskSpec) -> list[LeakageFinding]:
    """고유 ID 과다 열/ID 와 동일한 열을 찾는다. 결과가 있으면 E_LEAKAGE 대상."""
    np = _np()
    n = int(len(df))
    findings: list[LeakageFinding] = []
    id_values = df[spec.id_column].astype(str).tolist() if spec.id_column in df.columns else None
    for col in spec.categorical_features:
        if col not in df.columns:
            continue
        s = df[col].astype(str)
        nu = int(s.nunique())
        if id_values is not None and s.tolist() == id_values:
            findings.append(LeakageFinding(column=col, reason="ID 열과 값이 동일", n_unique=nu, n_rows=n))
        elif n >= LEAKAGE_MIN_UNIQUE and nu >= LEAKAGE_MIN_UNIQUE and nu / n > LEAKAGE_UNIQUE_RATIO:
            findings.append(
                LeakageFinding(
                    column=col,
                    reason=f"고유값 비율 {nu}/{n} > {LEAKAGE_UNIQUE_RATIO} (ID 성격)",
                    n_unique=nu,
                    n_rows=n,
                )
            )
    for col in spec.numeric_features:
        if col not in df.columns:
            continue
        arr = np.asarray(df[col], dtype="float64")
        finite = arr[np.isfinite(arr)]
        if finite.size < LEAKAGE_MIN_UNIQUE or n < LEAKAGE_MIN_UNIQUE:
            continue
        nu = int(np.unique(finite).size)
        if nu != finite.size:
            continue
        is_int = bool(np.all(np.equal(np.mod(finite, 1), 0)))
        consecutive = is_int and (finite.max() - finite.min() + 1) == finite.size
        if consecutive:
            findings.append(
                LeakageFinding(
                    column=col, reason="모든 값이 고유한 연속 정수 (행 ID 성격)", n_unique=nu, n_rows=n
                )
            )
            continue
        if id_values is not None:
            try:
                id_num = np.asarray([float(v) for v in id_values], dtype="float64")
            except ValueError:
                id_num = None
            if id_num is not None and id_num.shape == arr.shape and np.array_equal(id_num, arr):
                findings.append(
                    LeakageFinding(column=col, reason="ID 열의 숫자 값과 동일", n_unique=nu, n_rows=n)
                )
    return findings


def assert_no_leakage(df: pd.DataFrame, spec: TaskSpec) -> None:
    findings = check_leakage(df, spec)
    if findings:
        raise AgentError(
            "E_LEAKAGE",
            "ID 성격의 열이 feature 에 포함되어 있습니다: " + ", ".join(f.column for f in findings),
            details={"findings": [f.model_dump(mode="json") for f in findings]},
        )


class FittedPreprocessor:
    """학습된 전처리 상태 (sklearn 객체 없이 numpy 로 transform). pickle 을 사용하지 않는다."""

    def __init__(
        self,
        *,
        numeric_columns: list[str],
        categorical_columns: list[str],
        medians: Any,
        means: Any,
        scales: Any,
        categories: list[list[str]],
        infrequent: list[list[str]],
        max_categories: int,
        n_train_rows: int,
        taskspec_hash: str,
        data_origin: str,
        fitted_at: str | None = None,
    ) -> None:
        np = _np()
        self.numeric_columns = list(numeric_columns)
        self.categorical_columns = list(categorical_columns)
        self.medians = np.asarray(medians, dtype="float64").reshape(-1)
        self.means = np.asarray(means, dtype="float64").reshape(-1)
        self.scales = np.asarray(scales, dtype="float64").reshape(-1)
        self.categories = [[str(c) for c in cats] for cats in categories]
        self.infrequent = [[str(c) for c in infs] for infs in infrequent]
        self.max_categories = int(max_categories)
        self.n_train_rows = int(n_train_rows)
        self.taskspec_hash = taskspec_hash
        self.data_origin = data_origin
        self.fitted_at = fitted_at or now_iso()
        n_num = len(self.numeric_columns)
        if not (self.medians.size == self.means.size == self.scales.size == n_num):
            raise AgentError("E_SCHEMA_INVALID", "전처리 상태의 숫자 통계 길이가 열 수와 다릅니다")
        if len(self.categories) != len(self.categorical_columns) or len(self.infrequent) != len(
            self.categorical_columns
        ):
            raise AgentError("E_SCHEMA_INVALID", "전처리 상태의 범주 목록 길이가 열 수와 다릅니다")
        self._frequent: list[list[str]] = []
        self._index: list[dict[str, int]] = []
        for cats, infs in zip(self.categories, self.infrequent, strict=True):
            inf_set = set(infs)
            freq = [c for c in cats if c not in inf_set]
            self._frequent.append(freq)
            self._index.append({c: i for i, c in enumerate(freq)})
        self.feature_names: list[str] = [f"num__{c}" for c in self.numeric_columns]
        for col, freq, infs in zip(self.categorical_columns, self._frequent, self.infrequent, strict=True):
            self.feature_names += [f"cat__{col}_{c}" for c in freq]
            if infs:
                self.feature_names.append(f"cat__{col}_{INFREQUENT_NAME}")

    # ---- 크기 -----------------------------------------------------------------------
    @property
    def n_features(self) -> int:
        return len(self.feature_names)

    def estimate_dense_bytes(self, n_rows: int) -> int:
        return int(n_rows) * self.n_features * 4

    # ---- transform -------------------------------------------------------------------
    def transform_with_report(
        self, df: pd.DataFrame, *, allow_missing: bool = False, max_dense_bytes: int = DEFAULT_MAX_DENSE_BYTES
    ) -> tuple[np.ndarray, TransformReport]:
        np = _np()
        missing_cols = [c for c in self.numeric_columns + self.categorical_columns if c not in df.columns]
        if missing_cols:
            raise AgentError(
                "E_INPUT_INVALID",
                f"전처리에 필요한 열이 없습니다: {missing_cols}",
                details={"missing_columns": missing_cols},
            )
        n = int(len(df))
        dense_bytes = self.estimate_dense_bytes(n)
        if dense_bytes > max_dense_bytes:
            raise AgentError(
                "E_MEMORY_LIMIT",
                f"dense 변환 크기 {dense_bytes:,} bytes 가 상한 {max_dense_bytes:,} bytes 를 초과합니다",
                details={
                    "n_rows": n,
                    "n_features": self.n_features,
                    "dense_bytes": dense_bytes,
                    "max_dense_bytes": max_dense_bytes,
                },
            )
        out = np.zeros((n, self.n_features), dtype="float32")
        imputed: dict[str, int] = {}
        unknown: dict[str, int] = {}
        col_pos = 0
        if self.numeric_columns:
            try:
                x = np.asarray(
                    df[self.numeric_columns].to_numpy(dtype="float64", na_value=float("nan")), dtype="float64"
                )
            except (TypeError, ValueError) as exc:
                raise AgentError(
                    "E_INPUT_INVALID",
                    "숫자 feature 에 숫자가 아닌 값이 있습니다",
                    details={"error": str(exc)[:300]},
                ) from exc
            if np.isinf(x).any():
                bad = sorted({self.numeric_columns[j] for j in np.where(np.isinf(x).any(axis=0))[0]})
                raise AgentError(
                    "E_INPUT_INVALID", f"숫자 feature 에 Inf 값이 있습니다: {bad}", details={"columns": bad}
                )
            nan_mask = np.isnan(x)
            if nan_mask.any():
                counts = {
                    self.numeric_columns[j]: int(nan_mask[:, j].sum())
                    for j in range(x.shape[1])
                    if nan_mask[:, j].any()
                }
                if not allow_missing:
                    rows = [int(i) for i in np.where(nan_mask.any(axis=1))[0][:20]]
                    raise AgentError(
                        "E_INPUT_INVALID",
                        f"숫자 feature 에 빈 값이 있습니다 (allow_missing=False): {sorted(counts)}",
                        details={"columns": counts, "rows": rows},
                    )
                imputed = counts
                x = np.where(nan_mask, self.medians[None, :], x)
            out[:, : len(self.numeric_columns)] = ((x - self.means[None, :]) / self.scales[None, :]).astype(
                "float32"
            )
            col_pos = len(self.numeric_columns)
        for col, freq, infs, index in zip(
            self.categorical_columns, self._frequent, self.infrequent, self._index, strict=True
        ):
            width = len(freq) + (1 if infs else 0)
            inf_col = col_pos + len(freq) if infs else None
            values = df[col].astype(str).tolist()
            n_unknown = 0
            inf_set = set(infs)
            for i, v in enumerate(values):
                j = index.get(v)
                if j is not None:
                    out[i, col_pos + j] = 1.0
                elif v in inf_set:
                    assert inf_col is not None
                    out[i, inf_col] = 1.0
                else:
                    n_unknown += 1
                    if inf_col is not None:
                        out[i, inf_col] = 1.0
            if n_unknown:
                unknown[col] = n_unknown
            col_pos += width
        if not np.isfinite(out).all():
            raise AgentError("E_INTERNAL", "전처리 출력에 비유한 값이 있습니다")
        report = TransformReport(
            n_rows=n,
            n_features=self.n_features,
            unknown_categories=unknown,
            imputed_values=imputed,
            dense_bytes=dense_bytes,
        )
        return out, report

    def transform(
        self, df: pd.DataFrame, *, allow_missing: bool = False, max_dense_bytes: int = DEFAULT_MAX_DENSE_BYTES
    ) -> np.ndarray:
        x, _ = self.transform_with_report(df, allow_missing=allow_missing, max_dense_bytes=max_dense_bytes)
        return x

    # ---- 상태 직렬화 -----------------------------------------------------------------
    def to_state(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(meta(json 가능), arrays(np.ndarray)) — pickle 없음."""
        meta: dict[str, Any] = {
            "format": STATE_FORMAT,
            "numeric_columns": list(self.numeric_columns),
            "categorical_columns": list(self.categorical_columns),
            "categories": [list(c) for c in self.categories],
            "infrequent": [list(c) for c in self.infrequent],
            "feature_names": list(self.feature_names),
            "max_categories": self.max_categories,
            "n_train_rows": self.n_train_rows,
            "taskspec_hash": self.taskspec_hash,
            "data_origin": self.data_origin,
            "synthetic": self.data_origin == "synthetic",
            "fitted_at": self.fitted_at,
            "array_keys": ["medians", "means", "scales"],
        }
        arrays = {"medians": self.medians.copy(), "means": self.means.copy(), "scales": self.scales.copy()}
        return meta, arrays

    @classmethod
    def from_state(cls, meta: dict[str, Any], arrays: dict[str, Any]) -> FittedPreprocessor:
        if not isinstance(meta, dict) or meta.get("format") != STATE_FORMAT:
            raise AgentError(
                "E_ARTIFACT_UNTRUSTED",
                "전처리 상태 형식이 이 프로그램의 것이 아닙니다",
                details={"format": str(meta.get("format")) if isinstance(meta, dict) else None},
            )
        required = (
            "numeric_columns",
            "categorical_columns",
            "categories",
            "infrequent",
            "feature_names",
            "max_categories",
            "n_train_rows",
            "taskspec_hash",
            "data_origin",
        )
        missing = [k for k in required if k not in meta]
        if missing:
            raise AgentError("E_SCHEMA_INVALID", f"전처리 상태에 필드가 없습니다: {missing}")
        for key in ("medians", "means", "scales"):
            if key not in arrays:
                raise AgentError("E_SCHEMA_INVALID", f"전처리 배열이 없습니다: {key}")
        fp = cls(
            numeric_columns=[str(c) for c in meta["numeric_columns"]],
            categorical_columns=[str(c) for c in meta["categorical_columns"]],
            medians=arrays["medians"],
            means=arrays["means"],
            scales=arrays["scales"],
            categories=[[str(x) for x in c] for c in meta["categories"]],
            infrequent=[[str(x) for x in c] for c in meta["infrequent"]],
            max_categories=int(meta["max_categories"]),
            n_train_rows=int(meta["n_train_rows"]),
            taskspec_hash=str(meta["taskspec_hash"]),
            data_origin=str(meta["data_origin"]),
            fitted_at=str(meta.get("fitted_at") or now_iso()),
        )
        if list(meta["feature_names"]) != fp.feature_names:
            raise AgentError("E_SCHEMA_INVALID", "전처리 상태의 feature_names 가 재구성 결과와 다릅니다")
        return fp

    def save(self, directory: str | Path) -> dict[str, str]:
        """directory/preprocessor.json + preprocessor.npz 저장 (원자적). json 에 npz sha256 을 기록한다."""
        np = _np()
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        meta, arrays = self.to_state()
        buf = io.BytesIO()
        np.savez(buf, **arrays)
        npz_bytes = buf.getvalue()
        atomic_write_bytes(d / NPZ_NAME, npz_bytes)
        meta["npz_file"] = NPZ_NAME
        meta["npz_sha256"] = sha256_bytes(npz_bytes)
        atomic_write_json(d / JSON_NAME, meta)
        return {"json": str(d / JSON_NAME), "npz": str(d / NPZ_NAME)}

    @classmethod
    def load(cls, directory: str | Path) -> FittedPreprocessor:
        np = _np()
        d = Path(directory)
        jp, npz_p = d / JSON_NAME, d / NPZ_NAME
        if not jp.is_file() or not npz_p.is_file():
            raise AgentError(
                "E_ARTIFACT_UNTRUSTED",
                "전처리 상태 파일(preprocessor.json/npz)이 없습니다",
                details={"dir": str(d)},
            )
        meta = read_json(jp)
        raw = npz_p.read_bytes()
        if meta.get("npz_sha256") != sha256_bytes(raw):
            raise AgentError("E_ARTIFACT_UNTRUSTED", "preprocessor.npz 의 checksum 이 일치하지 않습니다")
        with np.load(io.BytesIO(raw), allow_pickle=False) as z:
            arrays = {k: np.asarray(z[k]) for k in z.files}
        return cls.from_state(meta, arrays)


def fit_preprocessor(
    spec: TaskSpec, train_df: pd.DataFrame, *, max_categories: int = MAX_CATEGORIES
) -> FittedPreprocessor:
    """train 데이터에만 fit 한다. 호출자는 validation/test 를 넘기면 안 된다 (열 검사 외에는 강제할 수 없으므로 문서화)."""
    np = _np()
    missing = [c for c in spec.features if c not in train_df.columns]
    if missing:
        raise AgentError("E_INPUT_INVALID", f"train 데이터에 feature 열이 없습니다: {missing}")
    if len(train_df) == 0:
        raise AgentError("E_INPUT_INVALID", "train 데이터가 비어 있습니다")
    assert_no_leakage(train_df, spec)
    ct = build_preprocessor(spec, max_categories=max_categories)
    fit_frame = train_df[spec.features].copy()
    for col in spec.numeric_features:
        fit_frame[col] = np.asarray(fit_frame[col], dtype="float64")
        if np.isinf(fit_frame[col].to_numpy()).any():
            raise AgentError("E_INPUT_INVALID", f"train 의 숫자 feature '{col}' 에 Inf 가 있습니다")
        if np.isnan(fit_frame[col].to_numpy()).all():
            raise AgentError("E_INPUT_INVALID", f"train 의 숫자 feature '{col}' 이 모두 비어 있습니다")
    for col in spec.categorical_features:
        fit_frame[col] = fit_frame[col].astype(str)
    ct.fit(fit_frame)
    medians = np.zeros(0)
    means = np.zeros(0)
    scales = np.zeros(0)
    categories: list[list[str]] = []
    infrequent: list[list[str]] = []
    if spec.numeric_features:
        pipe = ct.named_transformers_["num"]
        medians = np.asarray(pipe.named_steps["impute"].statistics_, dtype="float64")
        means = np.asarray(pipe.named_steps["scale"].mean_, dtype="float64")
        scales = np.asarray(pipe.named_steps["scale"].scale_, dtype="float64")
    if spec.categorical_features:
        ohe = ct.named_transformers_["cat"]
        infreq_attr = getattr(ohe, "infrequent_categories_", None)
        for i, cats in enumerate(ohe.categories_):
            categories.append([str(c) for c in cats])
            inf = None if infreq_attr is None else infreq_attr[i]
            infrequent.append([] if inf is None else [str(c) for c in inf])
    fp = FittedPreprocessor(
        numeric_columns=list(spec.numeric_features),
        categorical_columns=list(spec.categorical_features),
        medians=medians,
        means=means,
        scales=scales,
        categories=categories,
        infrequent=infrequent,
        max_categories=max_categories,
        n_train_rows=int(len(train_df)),
        taskspec_hash=taskspec_hash(spec),
        data_origin=spec.data_origin,
    )
    expected = [str(n) for n in ct.get_feature_names_out()]
    if expected != fp.feature_names:
        raise AgentError(
            "E_INTERNAL",
            "전처리 feature 이름이 sklearn 과 일치하지 않습니다",
            details={"sklearn": expected[:10], "ours": fp.feature_names[:10]},
        )
    return fp


def is_finite_array(x: Any) -> bool:
    np = _np()
    arr = np.asarray(x)
    return bool(np.isfinite(arr).all()) if arr.size else True


def dense_bytes(n_rows: int, n_features: int, itemsize: int = 4) -> int:
    return int(math.prod((n_rows, n_features, itemsize)))
