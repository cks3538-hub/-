"""기준 모델 (scikit-learn). 기준 모델이 우수하면 그 모델이 선택될 수 있어야 한다.

회귀: DummyRegressor / Ridge / HistGradientBoostingRegressor
분류: DummyClassifier / LogisticRegression / HistGradientBoostingClassifier
입력은 전처리된 dense float32 (n, d). 분류 predict 는 양성 확률을 돌려준다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from corp_dl_agent.errors import AgentError, blocked_dependency

if TYPE_CHECKING:
    import numpy as np

REGRESSION_BASELINES: tuple[str, ...] = ("DummyRegressor", "Ridge", "HistGradientBoostingRegressor")
CLASSIFICATION_BASELINES: tuple[str, ...] = (
    "DummyClassifier",
    "LogisticRegression",
    "HistGradientBoostingClassifier",
)


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "기준 모델") from exc
    return np


def baseline_names(task_type: str) -> tuple[str, ...]:
    if task_type == "regression":
        return REGRESSION_BASELINES
    if task_type == "binary_classification":
        return CLASSIFICATION_BASELINES
    raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")


def baseline_models(task_type: str, *, seed: int = 42) -> dict[str, Any]:
    """이름 -> 미학습 estimator. 결정적(random_state 고정)."""
    try:
        from sklearn.dummy import DummyClassifier, DummyRegressor
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
        from sklearn.linear_model import LogisticRegression, Ridge
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("scikit-learn", "기준 모델") from exc
    if task_type == "regression":
        return {
            "DummyRegressor": DummyRegressor(strategy="mean"),
            "Ridge": Ridge(alpha=1.0),
            "HistGradientBoostingRegressor": HistGradientBoostingRegressor(
                max_iter=200, learning_rate=0.05, random_state=seed
            ),
        }
    if task_type == "binary_classification":
        return {
            "DummyClassifier": DummyClassifier(strategy="prior"),
            "LogisticRegression": LogisticRegression(max_iter=2000, C=1.0),
            "HistGradientBoostingClassifier": HistGradientBoostingClassifier(
                max_iter=200, learning_rate=0.05, random_state=seed
            ),
        }
    raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")


def _check_xy(X: Any, y: Any | None, task_type: str | None) -> tuple[Any, Any | None]:
    np = _np()
    Xa = np.asarray(X)
    if Xa.ndim != 2:
        raise AgentError("E_INPUT_INVALID", f"X 는 2차원 배열이어야 합니다 (shape={Xa.shape})")
    if Xa.shape[0] == 0:
        raise AgentError("E_INPUT_INVALID", "X 에 표본이 없습니다")
    if not np.issubdtype(Xa.dtype, np.floating):
        Xa = Xa.astype("float32")
    if not np.isfinite(Xa).all():
        raise AgentError("E_INPUT_INVALID", "X 에 NaN/Inf 가 있습니다")
    ya = None
    if y is not None:
        ya = np.asarray(y).reshape(-1)
        if ya.shape[0] != Xa.shape[0]:
            raise AgentError("E_INPUT_INVALID", f"X/y 길이가 다릅니다: {Xa.shape[0]} vs {ya.shape[0]}")
        if task_type == "regression":
            ya = ya.astype("float64")
            if not np.isfinite(ya).all():
                raise AgentError("E_INPUT_INVALID", "y 에 NaN/Inf 가 있습니다")
        elif task_type == "binary_classification":
            ya = ya.astype("int64")
            if not set(np.unique(ya).tolist()) <= {0, 1}:
                raise AgentError("E_INPUT_INVALID", "이진 분류 y 는 0/1 이어야 합니다")
    return Xa, ya


def fit_baseline(name: str, est: Any, X_train: Any, y_train: Any, *, task_type: str | None = None) -> Any:
    """estimator 를 train 에 fit 한다 (shape/dtype/finite 검사). 반환: 학습된 estimator."""
    kind = task_type
    if kind is None:
        kind = (
            "regression"
            if name in REGRESSION_BASELINES
            else ("binary_classification" if name in CLASSIFICATION_BASELINES else None)
        )
    Xa, ya = _check_xy(X_train, y_train, kind)
    if kind == "binary_classification" and ya is not None and len(set(ya.tolist())) < 2:
        raise AgentError("E_INPUT_INVALID", f"'{name}' 학습 데이터에 클래스가 하나뿐입니다")
    est.fit(Xa, ya)
    return est


def predict_baseline(est: Any, X: Any, task_type: str) -> np.ndarray:
    """회귀: 예측값 (n,) float64. 분류: 양성(1) 확률 (n,) float64."""
    np = _np()
    Xa, _ = _check_xy(X, None, None)
    if task_type == "regression":
        pred = np.asarray(est.predict(Xa), dtype="float64").reshape(-1)
    elif task_type == "binary_classification":
        if not hasattr(est, "predict_proba"):
            raise AgentError(
                "E_NOT_SUPPORTED", f"{type(est).__name__} 는 확률 예측(predict_proba)을 지원하지 않습니다"
            )
        proba = np.asarray(est.predict_proba(Xa), dtype="float64")
        classes = [int(c) for c in getattr(est, "classes_", [0, 1])]
        if 1 in classes:
            pred = proba[:, classes.index(1)]
        else:
            pred = np.zeros(Xa.shape[0], dtype="float64")
    else:
        raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {task_type}")
    if not np.isfinite(pred).all():
        raise AgentError("E_INTERNAL", f"{type(est).__name__} 예측에 NaN/Inf 가 있습니다")
    return pred


def baseline_params(est: Any) -> dict[str, Any]:
    """plan.json 기록용 파라미터 (JSON 직렬화 가능한 값만)."""
    out: dict[str, Any] = {}
    for k, v in est.get_params(deep=False).items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        else:
            out[k] = repr(v)
    return out
