"""평가 지표 (정의를 명시한다).

회귀 (원단위): mae, rmse, r2, p95_abs_error (절대오차의 95 백분위수, numpy linear 보간), max_abs_error
분류: average_precision, roc_auc (확률 순위 기반), precision/recall/f1 (predicted positive := probability >= threshold),
     confusion_matrix {tn, fp, fn, tp}. threshold 는 validation 에서만 고른다 (choose_threshold).
acceptance 가 없으면 NEEDS_ACCEPTANCE_CRITERIA 이며 임의 값을 만들지 않는다.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from corp_dl_agent.common import StrictModel
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.ml.taskspec import METRIC_DIRECTION, NEEDS_ACCEPTANCE_CRITERIA, Acceptance

AcceptanceState = Literal["PASS", "FAIL", "NEEDS_ACCEPTANCE_CRITERIA", "METRIC_MISSING"]


class AcceptanceResult(StrictModel):
    state: AcceptanceState
    metric: str | None = None
    threshold: float | None = None
    direction: str | None = None
    observed: float | None = None
    message: str


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "평가") from exc
    return np


def _metrics_mod() -> Any:
    try:
        from sklearn import metrics
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("scikit-learn", "평가") from exc
    return metrics


def _pair(y_true: Any, y_pred: Any, *, name: str) -> tuple[Any, Any]:
    np = _np()
    a = np.asarray(y_true, dtype="float64").reshape(-1)
    b = np.asarray(y_pred, dtype="float64").reshape(-1)
    if a.shape != b.shape:
        raise AgentError(
            "E_INPUT_INVALID", f"{name}: y_true/y_pred 길이가 다릅니다 ({a.shape[0]} vs {b.shape[0]})"
        )
    if a.size == 0:
        raise AgentError("E_INPUT_INVALID", f"{name}: 평가할 표본이 없습니다")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise AgentError("E_INPUT_INVALID", f"{name}: y_true/y_pred 에 NaN/Inf 가 있습니다")
    return a, b


def evaluate_regression(y_true: Any, y_pred: Any, unit: str = "") -> dict[str, Any]:
    np = _np()
    m = _metrics_mod()
    a, b = _pair(y_true, y_pred, name="evaluate_regression")
    abs_err = np.abs(a - b)
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    r2 = float(m.r2_score(a, b)) if ss_tot > 0 else float("nan")
    return {
        "n": int(a.size),
        "unit": unit,
        "mae": float(m.mean_absolute_error(a, b)),
        "rmse": float(math.sqrt(m.mean_squared_error(a, b))),
        "r2": r2,
        "p95_abs_error": float(np.percentile(abs_err, 95)),
        "max_abs_error": float(abs_err.max()),
        "definitions": {
            "mae": "mean(|y - yhat|) (원단위)",
            "rmse": "sqrt(mean((y - yhat)^2)) (원단위)",
            "r2": "1 - SS_res/SS_tot (y_true 가 상수면 정의되지 않아 NaN)",
            "p95_abs_error": "|y - yhat| 의 95 백분위수 (numpy percentile, linear)",
        },
    }


def _binary_true(y_true: Any, *, name: str) -> Any:
    np = _np()
    a = np.asarray(y_true).reshape(-1)
    if a.size == 0:
        raise AgentError("E_INPUT_INVALID", f"{name}: 평가할 표본이 없습니다")
    try:
        ai = a.astype("int64")
    except (TypeError, ValueError) as exc:
        raise AgentError("E_INPUT_INVALID", f"{name}: y_true 는 0/1 이어야 합니다") from exc
    if not np.array_equal(ai, a) or not set(np.unique(ai).tolist()) <= {0, 1}:
        raise AgentError("E_INPUT_INVALID", f"{name}: y_true 는 0/1 이어야 합니다")
    return ai


def evaluate_classification(y_true: Any, y_prob: Any, threshold: float) -> dict[str, Any]:
    np = _np()
    m = _metrics_mod()
    yt = _binary_true(y_true, name="evaluate_classification")
    p = np.asarray(y_prob, dtype="float64").reshape(-1)
    if p.shape != yt.shape:
        raise AgentError(
            "E_INPUT_INVALID",
            f"evaluate_classification: y_true/y_prob 길이가 다릅니다 ({yt.shape[0]} vs {p.shape[0]})",
        )
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise AgentError(
            "E_INPUT_INVALID", "evaluate_classification: y_prob 는 [0,1] 범위의 유한한 확률이어야 합니다"
        )
    if not (isinstance(threshold, (int, float)) and math.isfinite(threshold) and 0.0 <= threshold <= 1.0):
        raise AgentError("E_INPUT_INVALID", f"threshold 는 [0,1] 범위여야 합니다: {threshold}")
    pred = (p >= threshold).astype("int64")
    tp = int(((pred == 1) & (yt == 1)).sum())
    tn = int(((pred == 0) & (yt == 0)).sum())
    fp = int(((pred == 1) & (yt == 0)).sum())
    fn = int(((pred == 0) & (yt == 1)).sum())
    n_pos = int((yt == 1).sum())
    n_neg = int((yt == 0).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    both_classes = n_pos > 0 and n_neg > 0
    ap = float(m.average_precision_score(yt, p)) if n_pos > 0 else float("nan")
    auc = float(m.roc_auc_score(yt, p)) if both_classes else float("nan")
    return {
        "n": int(yt.size),
        "positives": n_pos,
        "negatives": n_neg,
        "threshold": float(threshold),
        "average_precision": ap,
        "roc_auc": auc,
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "accuracy": float((tp + tn) / yt.size),
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "definitions": {
            "positive_rule": "predicted positive := probability >= threshold",
            "precision": "tp/(tp+fp) (분모 0 이면 0)",
            "recall": "tp/(tp+fn) (분모 0 이면 0)",
            "f1": "2PR/(P+R) (P+R=0 이면 0)",
            "average_precision": "sklearn average_precision_score (양성이 없으면 NaN)",
            "roc_auc": "sklearn roc_auc_score (한 클래스만 있으면 NaN)",
        },
    }


def choose_threshold(y_val_true: Any, y_val_prob: Any, metric: str = "f1") -> float:
    """validation 확률로 임계값을 고른다 (test 사용 금지).

    후보: validation 의 고유 확률값. 선택: metric 최대 (동률이면 0.5 에 가까운 값, 그다음 큰 값).
    """
    np = _np()
    yt = _binary_true(y_val_true, name="choose_threshold")
    p = np.asarray(y_val_prob, dtype="float64").reshape(-1)
    if p.shape != yt.shape:
        raise AgentError("E_INPUT_INVALID", "choose_threshold: y_true/y_prob 길이가 다릅니다")
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise AgentError("E_INPUT_INVALID", "choose_threshold: y_prob 는 [0,1] 범위여야 합니다")
    if metric not in ("f1", "balanced_accuracy"):
        raise AgentError(
            "E_SCHEMA_INVALID", f"지원하지 않는 threshold metric: {metric} (f1|balanced_accuracy)"
        )
    n_pos = int(yt.sum())
    n_neg = int(yt.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        raise AgentError(
            "E_INPUT_INVALID",
            "validation 에 양성/음성 표본이 모두 있어야 임계값을 고를 수 있습니다",
            details={"positives": n_pos, "negatives": n_neg},
        )
    order = np.argsort(-p, kind="stable")
    ps = p[order]
    ys = yt[order]
    tp_cum = np.cumsum(ys)
    fp_cum = np.cumsum(1 - ys)
    # 각 고유 확률값 t 에 대해 "p >= t" 인 마지막 index
    last_idx = np.flatnonzero(np.r_[ps[1:] != ps[:-1], True])
    tp = tp_cum[last_idx].astype("float64")
    fp = fp_cum[last_idx].astype("float64")
    fn = n_pos - tp
    tn = n_neg - fp
    if metric == "f1":
        score = np.where((2 * tp + fp + fn) > 0, 2 * tp / np.maximum(2 * tp + fp + fn, 1), 0.0)
    else:
        score = 0.5 * (tp / n_pos + tn / n_neg)
    thresholds = ps[last_idx]
    best = float(score.max())
    cand = thresholds[np.isclose(score, best, rtol=0.0, atol=1e-12)]
    dist = np.abs(cand - 0.5)
    closest = cand[np.isclose(dist, dist.min(), rtol=0.0, atol=1e-12)]
    return float(closest.max())


def is_finite_metrics(d: Any) -> bool:
    """dict/list 안의 모든 숫자(bool 제외)가 유한하면 True. None 은 비유한으로 본다. 문자열/bool 은 무시."""
    if isinstance(d, dict):
        return all(is_finite_metrics(v) for v in d.values())
    if isinstance(d, (list, tuple)):
        return all(is_finite_metrics(v) for v in d)
    if d is None:
        return False
    if isinstance(d, bool) or isinstance(d, str):
        return True
    if isinstance(d, (int, float)):
        return math.isfinite(d)
    try:
        return bool(math.isfinite(float(d)))
    except (TypeError, ValueError):
        return True


def is_better(new: float, old: float | None, direction: str) -> bool:
    if old is None or not math.isfinite(old):
        return math.isfinite(new)
    if not math.isfinite(new):
        return False
    return new < old if direction == "min" else new > old


def select_best(
    results: dict[str, dict[str, Any]], metric: str, direction: str | None = None
) -> tuple[str, float]:
    """validation metric 으로 후보(기준 모델 포함)를 고른다. 동률이면 이름 정렬 순으로 결정적."""
    d = direction or METRIC_DIRECTION.get(metric)
    if d not in ("min", "max"):
        raise AgentError("E_SCHEMA_INVALID", f"direction 을 알 수 없습니다: {metric}")
    best_name: str | None = None
    best_val: float | None = None
    for name in sorted(results):
        val = results[name].get(metric)
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
            continue
        if best_val is None or is_better(float(val), best_val, d):
            best_name, best_val = name, float(val)
    if best_name is None or best_val is None:
        raise AgentError(
            "E_INPUT_INVALID",
            f"유한한 '{metric}' 값을 가진 후보가 없습니다",
            details={"candidates": sorted(results)},
        )
    return best_name, best_val


def acceptance_status(acceptance: Acceptance | None, metrics: dict[str, Any]) -> AcceptanceResult:
    """업무 허용 기준 판정. acceptance 가 없으면 NEEDS_ACCEPTANCE_CRITERIA (임의 기준을 만들지 않는다)."""
    if acceptance is None:
        return AcceptanceResult(
            state=NEEDS_ACCEPTANCE_CRITERIA,
            message="업무 허용오차(acceptance)가 입력되지 않아 합격 여부를 판정하지 않습니다",
        )
    val = metrics.get(acceptance.metric)
    if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
        return AcceptanceResult(
            state="METRIC_MISSING",
            metric=acceptance.metric,
            threshold=acceptance.threshold,
            direction=acceptance.direction,
            observed=None,
            message=f"metric '{acceptance.metric}' 값이 없거나 유한하지 않아 판정할 수 없습니다",
        )
    ok = (
        float(val) <= acceptance.threshold
        if acceptance.direction == "min"
        else float(val) >= acceptance.threshold
    )
    return AcceptanceResult(
        state="PASS" if ok else "FAIL",
        metric=acceptance.metric,
        threshold=acceptance.threshold,
        direction=acceptance.direction,
        observed=float(val),
        message=f"{acceptance.metric}={float(val):.6g} {'<=' if acceptance.direction == 'min' else '>='} {acceptance.threshold:.6g} → {'PASS' if ok else 'FAIL'}",
    )


def summarize_for_report(metrics: dict[str, Any]) -> dict[str, Any]:
    """정의 문자열을 제외한 숫자 요약 (보고서/모델 카드용)."""
    return {k: v for k, v in metrics.items() if k != "definitions"}
