"""후보(MLP) 학습기: epoch 단위 checkpoint, pause/cancel/budget 경계, OOM/NaN 재시도(attempt), 재개.

규칙 (BUILD_SPEC [8], [9]):
- CPU FP32 기본. CUDA/AMP 는 device=="cuda" 이고 torch.cuda.is_available() 일 때만. GradScaler 는 AMP 일 때만, clipping 은 unscale 뒤.
- model.train()/eval() 구분, 예측은 torch.inference_mode 안에서 detach().cpu(), zero_grad(set_to_none=True).
- 손실은 reduction="sum" 으로 계산하고 accumulation 묶음의 실제 표본 수로 나눈다 (마지막 불완전 묶음 포함).
- finite 검사: loss 와 gradient norm. NaN/Inf → 진단 기록 후 LR 절반(+AMP 해제) 새 attempt 최대 nan_retries(1) 회.
- OOM(RuntimeError 'out of memory' / torch OutOfMemoryError / 모의 SimulatedOOM) → 실패 attempt 정리 후
  microbatch 절반·accumulation 2배로 새 attempt 최대 oom_retries(2) 회. 변경은 attempt.changes 에 기록한다.
  모의 주입(SimulatedOOM) 과 실제 OOM 은 `oom_simulated` 로 구분한다. 동일 수학 실험이라고 주장하지 않는다.
- epoch 완료 시 latest.pt (개선 시 best.pt) 저장 → 그 뒤 heartbeat/예산/cancel/pause/early-stop 검사.
  pause 는 epoch 경계에서만, 강제 종료 후 resume 은 마지막 완료 epoch 뒤부터 (진행 중 epoch 는 다시 계산).
- checkpoint 의 config_hash 는 trial fingerprint(task/data/split/code/lock/model/seed) 다. 다르면 E_FINGERPRINT_CHANGED.
- scalar 로그는 log_every 배치마다 item(). seed(python/numpy/torch/DataLoader generator) 를 기록한다. num_workers=0.
"""

from __future__ import annotations

import gc
import math
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from corp_dl_agent.common import StrictModel, dump_json, now_iso, sha256_file
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.logs import EventLog
from corp_dl_agent.ml.checkpoint import (
    BEST_NAME,
    LATEST_NAME,
    LoadedCheckpoint,
    capture_rng,
    load_checkpoint,
    restore_rng,
    save_checkpoint,
)
from corp_dl_agent.ml.evaluation import (
    choose_threshold,
    evaluate_classification,
    evaluate_regression,
    is_better,
    is_finite_metrics,
)
from corp_dl_agent.ml.mlp import (
    MlpArch,
    arch_from_candidate,
    build_mlp,
    check_output_target,
    count_parameters,
    make_loss,
    outputs_to_predictions,
    torch_module,
)
from corp_dl_agent.ml.registry import MlpCandidate
from corp_dl_agent.state.budget import BudgetTracker
from corp_dl_agent.state.coordinator import Coordinator, Lease, TrialRecord
from corp_dl_agent.state.machine import AttemptStatus, TrialStatus

if TYPE_CHECKING:
    pass

TrialStatusName = Literal["COMPLETED", "PAUSED", "CANCELLED", "FAILED", "BUDGET_EXCEEDED"]
GRAD_CLIP_MAX_NORM = 10.0
PREDICT_BATCH = 1024
MAX_HISTORY_IN_CHECKPOINT = 10000


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "MLP 학습") from exc
    return np


# ---------------------------------------------------------------------------- 시험용 모의 주입
class SimulatedOOM(RuntimeError):
    """시험에서 OOM 정책을 검증하기 위한 모의 예외 (메시지에 'out of memory' 포함). 실제 GPU OOM 과 구분 기록된다."""

    def __init__(self, message: str = "CUDA out of memory (simulated by test hook)") -> None:
        super().__init__(message)


class SimulatedCrash(BaseException):
    """강제 종료(kill) 시뮬레이션. Exception 이 아니므로 runner/trainer 가 상태를 정리하지 않고 그대로 전파한다."""


class NonFiniteError(Exception):
    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__(f"non-finite value: {diagnostics.get('where')}")
        self.diagnostics = diagnostics


@dataclass
class HookContext:
    run_id: str
    trial_id: str
    candidate_id: str
    attempt_no: int
    attempt_id: str
    coordinator: Coordinator
    budget: BudgetTracker


class TrainerHooks:
    """시험/진단용 hook. 기본 구현은 아무 것도 하지 않는다."""

    def on_batch(self, ctx: HookContext, epoch: int, batch_idx: int) -> None:  # noqa: B027 - 의도적 no-op 기본
        """각 배치의 forward 직전. SimulatedOOM 을 던져 OOM 정책을 시험할 수 있다."""

    def inject_non_finite(self, ctx: HookContext, epoch: int, batch_idx: int) -> bool:
        """True 를 돌려주면 해당 배치의 손실을 NaN 으로 만든다 (finite 검사 경로 시험)."""
        return False

    def on_epoch_end(self, ctx: HookContext, epoch: int, metrics: dict[str, Any]) -> None:  # noqa: B027
        """checkpoint 저장 직후, 경계 검사 직전. pause 요청·SimulatedCrash 등에 사용."""


# ---------------------------------------------------------------------------- 데이터
@dataclass
class TrainingData:
    """전처리된 dense float32 train/validation 데이터. 회귀 target 은 train 통계로 표준화하여 학습한다."""

    task_type: str
    X_train: Any
    y_train: Any
    X_val: Any
    y_val: Any
    feature_names: list[str]
    metric: str
    direction: str
    target_unit: str = ""
    target_mean: float | None = None
    target_std: float | None = None

    def __post_init__(self) -> None:
        np = _np()
        if self.task_type not in ("regression", "binary_classification"):
            raise AgentError("E_SCHEMA_INVALID", f"알 수 없는 task_type: {self.task_type}")
        self.X_train = np.ascontiguousarray(np.asarray(self.X_train, dtype="float32"))
        self.X_val = np.ascontiguousarray(np.asarray(self.X_val, dtype="float32"))
        self.y_train = np.asarray(self.y_train, dtype="float64").reshape(-1)
        self.y_val = np.asarray(self.y_val, dtype="float64").reshape(-1)
        for name, X, y in (("train", self.X_train, self.y_train), ("val", self.X_val, self.y_val)):
            if X.ndim != 2 or X.shape[0] == 0:
                raise AgentError("E_INPUT_INVALID", f"{name} X 는 비어 있지 않은 2차원 배열이어야 합니다")
            if X.shape[0] != y.shape[0]:
                raise AgentError(
                    "E_INPUT_INVALID", f"{name} X/y 길이가 다릅니다: {X.shape[0]} vs {y.shape[0]}"
                )
            if not np.isfinite(X).all() or not np.isfinite(y).all():
                raise AgentError("E_INPUT_INVALID", f"{name} 데이터에 NaN/Inf 가 있습니다")
        if self.X_train.shape[1] != self.X_val.shape[1]:
            raise AgentError("E_INPUT_INVALID", "train/val feature 수가 다릅니다")
        if len(self.feature_names) not in (0, int(self.X_train.shape[1])):
            raise AgentError("E_INPUT_INVALID", "feature_names 길이가 feature 수와 다릅니다")
        if self.task_type == "binary_classification":
            for name, y in (("train", self.y_train), ("val", self.y_val)):
                if not set(np.unique(y).tolist()) <= {0.0, 1.0}:
                    raise AgentError("E_INPUT_INVALID", f"{name} y 는 0/1 이어야 합니다")
            self.target_mean, self.target_std = None, None
        else:
            if self.target_mean is None:
                self.target_mean = float(self.y_train.mean())
            if self.target_std is None or not math.isfinite(self.target_std) or self.target_std <= 0:
                std = float(self.y_train.std())
                self.target_std = std if math.isfinite(std) and std > 0 else 1.0

    @property
    def in_dim(self) -> int:
        return int(self.X_train.shape[1])

    def target_inverse(self) -> dict[str, Any]:
        if self.task_type == "regression":
            return {
                "kind": "standardize",
                "mean": float(self.target_mean or 0.0),
                "std": float(self.target_std or 1.0),
                "unit": self.target_unit,
            }
        return {"kind": "identity", "mean": None, "std": None, "unit": ""}

    def model_targets(self, y: Any) -> Any:
        """모델이 학습하는 target (N,1) float32: 회귀는 표준화, 분류는 0/1."""
        np = _np()
        arr = np.asarray(y, dtype="float64").reshape(-1)
        if self.task_type == "regression":
            arr = (arr - float(self.target_mean or 0.0)) / float(self.target_std or 1.0)
        return np.ascontiguousarray(arr.astype("float32").reshape(-1, 1))

    def inverse(self, z: Any) -> Any:
        np = _np()
        arr = np.asarray(z, dtype="float64").reshape(-1)
        if self.task_type == "regression":
            return arr * float(self.target_std or 1.0) + float(self.target_mean or 0.0)
        return arr


# ---------------------------------------------------------------------------- 결과
class TrialResult(StrictModel):
    trial_id: str
    candidate_id: str
    run_id: str
    status: TrialStatusName
    epochs_completed: int = 0
    max_epochs: int
    best_epoch: int | None = None
    best_validation: float | None = None
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    threshold: float | None = None
    attempts: int = 0
    attempt_changes: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    seed: int
    device: str
    amp: bool = False
    microbatch: int
    accumulation: int = 1
    learning_rate: float
    parameters: int = 0
    elapsed_seconds: float = 0.0
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    best_checkpoint: str | None = None
    latest_checkpoint: str | None = None
    early_stopped: bool = False
    resumed_from_epoch: int | None = None
    reason: str = ""


# ---------------------------------------------------------------------------- 보조
def predict_in_batches(
    model: Any, X: Any, *, task_type: str, device: str = "cpu", batch_size: int = PREDICT_BATCH
) -> Any:
    """eval + inference_mode 예측. 배치 단위로 detach().cpu() 하여 모은다. 회귀는 모델 공간(표준화) 값, 분류는 확률."""
    torch = torch_module()
    np = _np()
    Xa = np.ascontiguousarray(np.asarray(X, dtype="float32"))
    model.eval()
    outs: list[Any] = []
    with torch.inference_mode():
        for i in range(0, int(Xa.shape[0]), int(batch_size)):
            xb = torch.from_numpy(Xa[i : i + int(batch_size)]).to(device)
            out = model(xb)
            outs.append(outputs_to_predictions(out, task_type).detach().cpu())
    if not outs:
        return np.zeros(0, dtype="float64")
    return torch.cat(outs).numpy().astype("float64")


def validation_metrics(data: TrainingData, raw_pred: Any) -> tuple[dict[str, Any], float | None]:
    """모델 출력(표준화 값/확률) → 원단위 metrics (definitions 제외). 분류는 validation 에서 임계값을 고른다."""
    np = _np()
    if data.task_type == "regression":
        m = evaluate_regression(data.y_val, data.inverse(raw_pred), data.target_unit)
        m.pop("definitions", None)
        return m, None
    prob = np.clip(np.asarray(raw_pred, dtype="float64"), 0.0, 1.0)
    yv = data.y_val.astype("int64")
    if 0 < int(yv.sum()) < int(yv.size):
        thr = float(choose_threshold(yv, prob, "f1"))
    else:
        thr = 0.5
    m = evaluate_classification(yv, prob, thr)
    m.pop("definitions", None)
    return m, thr


def _seed_all(seed: int) -> None:
    import random

    torch = torch_module()
    np = _np()
    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _is_oom(exc: BaseException) -> bool:
    if isinstance(exc, SimulatedOOM):
        return True
    torch = torch_module()
    oom_cls = getattr(torch, "OutOfMemoryError", None) or getattr(torch.cuda, "OutOfMemoryError", None)
    if oom_cls is not None and isinstance(exc, oom_cls):
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _batch_layout(n: int, microbatch: int, accumulation: int) -> tuple[list[int], list[int]]:
    """(각 배치 표본 수, 각 accumulation 묶음의 실제 표본 수). 마지막 불완전 묶음도 실제 수로 정규화한다."""
    sizes = [microbatch] * (n // microbatch)
    if n % microbatch:
        sizes.append(n % microbatch)
    groups = [sum(sizes[i : i + accumulation]) for i in range(0, len(sizes), accumulation)]
    return sizes, groups


def _emit(log: EventLog | None, event: str, **fields: Any) -> None:
    if log is not None:
        log.emit(event, **fields)


def _append_jsonl(path: Path | None, record: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(dump_json(record, indent=0).replace("\n", "") + "\n")


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def resolve_device(requested: str, *, amp: bool) -> tuple[str, bool, list[str]]:
    """설정된 device/amp 를 실제 지원 여부로 확정한다. CUDA 미지원이면 cpu 로 내리고 경고를 남긴다."""
    torch = torch_module()
    warnings: list[str] = []
    device = "cpu"
    if requested == "cuda":
        if torch.cuda.is_available():
            device = "cuda"
        else:
            warnings.append(
                "ml.device=cuda 가 요청되었지만 torch.cuda.is_available() 가 False 라 cpu 로 실행합니다"
            )
    use_amp = bool(amp and device == "cuda")
    if amp and device != "cuda":
        warnings.append("ml.amp=true 는 CUDA 에서만 적용됩니다 (CPU FP32 로 실행)")
    return device, use_amp, warnings


def _ensure_trial(
    coordinator: Coordinator, run_id: str, trial_id: str, candidate: MlpCandidate, fingerprint: str
) -> TrialRecord:
    try:
        trial = coordinator.get_trial(trial_id)
    except AgentError as exc:
        if exc.code != "E_INPUT_INVALID":
            raise
        trial = coordinator.create_trial(run_id, trial_id, candidate.model_dump(mode="json"), fingerprint)
    if trial.fingerprint != fingerprint:
        raise AgentError(
            "E_FINGERPRINT_CHANGED",
            f"trial {trial_id} 의 fingerprint 가 현재 실험과 다릅니다",
            details={"stored": trial.fingerprint[:16], "current": fingerprint[:16]},
        )
    if trial.status in (
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.SKIPPED,
        TrialStatus.CANCELLED,
    ):
        raise AgentError(
            "E_STATE_TRANSITION",
            f"종료된 trial 은 다시 학습하지 않습니다: {trial_id} ({trial.status.value})",
            details={"trial_id": trial_id, "status": trial.status.value},
        )
    return trial


@dataclass
class _AttemptSettings:
    microbatch: int
    accumulation: int
    learning_rate: float
    use_amp: bool
    attempt_no: int = 0
    changes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _EpochOutcome:
    """attempt 실행 결과. status None 이면 모든 epoch 를 마쳤다(완료 처리로 이동)."""

    status: TrialStatusName | None
    epochs_completed: int
    best_epoch: int | None
    best_validation: float | None
    history: list[dict[str, Any]]
    early_stopped: bool
    resumed_from: int | None
    parameters: int
    reason: str = ""


# ---------------------------------------------------------------------------- 학습
def train_candidate(
    candidate: MlpCandidate,
    data: TrainingData,
    budget: BudgetTracker,
    coordinator: Coordinator,
    run_dir: str | Path,
    *,
    device: str = "cpu",
    resume: bool = True,
    run_id: str,
    fingerprint: str,
    config_hash: str | None = None,
    trial_id: str | None = None,
    max_epochs: int = 10,
    patience: int = 3,
    amp: bool = False,
    seed: int = 42,
    hooks: TrainerHooks | None = None,
    event_log: EventLog | None = None,
    epoch_metrics_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    oom_retries: int = 2,
    nan_retries: int = 1,
    log_every: int = 10,
    lease: Lease | None = None,
) -> TrialResult:
    """한 MLP 후보를 학습한다. 반환 status: COMPLETED / PAUSED / CANCELLED / BUDGET_EXCEEDED / FAILED.

    PAUSED/BUDGET_EXCEEDED 는 열린 attempt 를 정리(cleanup_own → trial INTERRUPTED) 하고 돌아오며, 다음 호출(resume=True) 이
    latest.pt 뒤 epoch 부터 잇는다. FAILED 는 재시도 한도를 넘긴 OOM/NaN 이다.
    """
    torch_module()  # torch 가용성 검사 (없으면 E_BLOCKED_DEPENDENCY)
    run_dir = Path(run_dir)
    artifact_root_p = Path(artifact_root) if artifact_root is not None else run_dir
    trial_id = trial_id or f"{run_id}-{candidate.candidate_id}"
    config_hash = config_hash or fingerprint
    hooks = hooks or TrainerHooks()
    ckpt_dir = run_dir / "checkpoints" / trial_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = Path(epoch_metrics_path) if epoch_metrics_path is not None else None
    if max_epochs < 1 or patience < 1:
        raise AgentError("E_SCHEMA_INVALID", "max_epochs/patience 는 1 이상이어야 합니다")
    device, use_amp, dev_warnings = resolve_device(device, amp=amp)
    for w in dev_warnings:
        _emit(event_log, "device_warning", trial_id=trial_id, message=w)
    budget.start()
    started = time.monotonic()
    trial = _ensure_trial(coordinator, run_id, trial_id, candidate, fingerprint)
    resume_effective = bool(resume or trial.attempts > 0)

    settings = _AttemptSettings(
        microbatch=int(candidate.batch_size),
        accumulation=1,
        learning_rate=float(candidate.learning_rate),
        use_amp=use_amp,
        attempt_no=int(trial.attempts),  # 이전 실행(강제 종료/pause) 의 attempt 수부터 이어서 센다
    )
    oom_count = 0
    nan_count = 0
    attempt_changes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    def base_result(status: TrialStatusName, **kw: Any) -> TrialResult:
        return TrialResult(
            trial_id=trial_id,
            candidate_id=candidate.candidate_id,
            run_id=run_id,
            status=status,
            max_epochs=int(max_epochs),
            attempts=settings.attempt_no,
            attempt_changes=list(attempt_changes),
            diagnostics=list(diagnostics),
            seed=int(seed),
            device=device,
            amp=settings.use_amp,
            microbatch=settings.microbatch,
            accumulation=settings.accumulation,
            learning_rate=settings.learning_rate,
            elapsed_seconds=round(time.monotonic() - started, 3),
            latest_checkpoint=str(ckpt_dir / LATEST_NAME) if (ckpt_dir / LATEST_NAME).is_file() else None,
            best_checkpoint=str(ckpt_dir / BEST_NAME) if (ckpt_dir / BEST_NAME).is_file() else None,
            **kw,
        )

    while True:
        settings.attempt_no += 1
        attempt_id = coordinator.create_attempt(trial_id, changes=settings.changes)
        ctx = HookContext(
            run_id=run_id,
            trial_id=trial_id,
            candidate_id=candidate.candidate_id,
            attempt_no=settings.attempt_no,
            attempt_id=attempt_id,
            coordinator=coordinator,
            budget=budget,
        )
        _emit(
            event_log,
            "attempt_started",
            trial_id=trial_id,
            attempt_id=attempt_id,
            attempt_no=settings.attempt_no,
            changes=settings.changes,
            resume=resume_effective,
        )
        try:
            outcome = _run_attempt(
                candidate=candidate,
                data=data,
                budget=budget,
                coordinator=coordinator,
                ckpt_dir=ckpt_dir,
                settings=settings,
                ctx=ctx,
                device=device,
                resume=resume_effective,
                config_hash=config_hash,
                max_epochs=max_epochs,
                patience=patience,
                seed=seed,
                hooks=hooks,
                event_log=event_log,
                metrics_path=metrics_path,
                log_every=log_every,
                lease=lease,
                lr_override=settings.learning_rate if settings.changes.get("learning_rate") else None,
            )
        except NonFiniteError as exc:
            nan_count += 1
            diag = {"kind": "non_finite", "attempt_no": settings.attempt_no, **exc.diagnostics}
            diagnostics.append(diag)
            _emit(
                event_log, "non_finite_detected", trial_id=trial_id, attempt_id=attempt_id, **exc.diagnostics
            )
            coordinator.finish_attempt(attempt_id, AttemptStatus.FAILED, reason="non_finite_loss_or_grad")
            _release_memory(device)
            if nan_count > nan_retries:
                reason = f"NaN/Inf 가 교정 한도({nan_retries}회) 뒤에도 발생했습니다"
                coordinator.finish_trial(trial_id, TrialStatus.FAILED, metrics={}, reason=reason)
                _emit(event_log, "trial_failed", trial_id=trial_id, reason=reason)
                return base_result("FAILED", reason=reason)
            new_lr = settings.learning_rate / 2.0
            change = {
                "reason": "non_finite",
                "learning_rate": {"from": settings.learning_rate, "to": new_lr},
                "amp": {"from": settings.use_amp, "to": False},
                "attempt_no": settings.attempt_no + 1,
                "diagnostics": exc.diagnostics,
            }
            settings.learning_rate = new_lr
            settings.use_amp = False
            settings.changes = change
            attempt_changes.append(change)
            resume_effective = True
            continue
        except Exception as exc:  # noqa: BLE001 - OOM 판별 후 나머지는 그대로 전파
            if not _is_oom(exc):
                coordinator.finish_attempt(attempt_id, AttemptStatus.FAILED, reason=f"{type(exc).__name__}")
                raise
            oom_count += 1
            simulated = isinstance(exc, SimulatedOOM)
            diag = {
                "kind": "oom",
                "attempt_no": settings.attempt_no,
                "oom_simulated": simulated,
                "message": str(exc)[:200],
                "microbatch": settings.microbatch,
                "accumulation": settings.accumulation,
            }
            diagnostics.append(diag)
            _emit(event_log, "oom_detected", trial_id=trial_id, attempt_id=attempt_id, **diag)
            coordinator.finish_attempt(
                attempt_id, AttemptStatus.FAILED, reason="oom_simulated" if simulated else "oom"
            )
            _release_memory(device)
            if oom_count > oom_retries or settings.microbatch <= 1:
                reason = f"OOM 이 재시도 한도({oom_retries}회) 를 넘었습니다"
                coordinator.finish_trial(trial_id, TrialStatus.FAILED, metrics={}, reason=reason)
                _emit(event_log, "trial_failed", trial_id=trial_id, reason=reason)
                return base_result("FAILED", reason=reason)
            new_micro = max(1, settings.microbatch // 2)
            new_accum = settings.accumulation * 2
            change = {
                "reason": "oom",
                "oom_simulated": simulated,
                "microbatch": {"from": settings.microbatch, "to": new_micro},
                "accumulation": {"from": settings.accumulation, "to": new_accum},
                "attempt_no": settings.attempt_no + 1,
                "note": "microbatch/accumulation 이 바뀌어 동일한 수학적 실험이 아니다",
            }
            settings.microbatch = new_micro
            settings.accumulation = new_accum
            settings.changes = change
            attempt_changes.append(change)
            resume_effective = True
            continue

        # ---- attempt 가 예외 없이 끝남
        if outcome.status is not None:
            if outcome.status == "CANCELLED":
                coordinator.finish_trial(trial_id, TrialStatus.CANCELLED, metrics={}, reason=outcome.reason)
            else:  # PAUSED / BUDGET_EXCEEDED: 열린 attempt 정리 → trial INTERRUPTED (재개 가능)
                coordinator.cleanup_own(run_id)
            _emit(
                event_log,
                "trial_interrupted",
                trial_id=trial_id,
                status=outcome.status,
                epochs_completed=outcome.epochs_completed,
                reason=outcome.reason,
            )
            return base_result(
                outcome.status,
                epochs_completed=outcome.epochs_completed,
                best_epoch=outcome.best_epoch,
                best_validation=outcome.best_validation,
                parameters=outcome.parameters,
                early_stopped=outcome.early_stopped,
                resumed_from_epoch=outcome.resumed_from,
                reason=outcome.reason,
            )

        # ---- 완료: best checkpoint 의 metrics 가 이 trial 의 validation 결과
        best_path = ckpt_dir / BEST_NAME
        if (
            outcome.best_validation is None
            or not math.isfinite(outcome.best_validation)
            or not best_path.is_file()
        ):
            reason = "유한한 validation metric 을 얻지 못했습니다 (best checkpoint 없음)"
            coordinator.finish_trial(trial_id, TrialStatus.FAILED, metrics={}, reason=reason)
            return base_result("FAILED", epochs_completed=outcome.epochs_completed, reason=reason)
        best_ck = load_checkpoint(best_path, config_hash)
        val_metrics = dict(best_ck.meta.metrics)
        threshold = val_metrics.get("threshold")
        if not is_finite_metrics({k: v for k, v in val_metrics.items() if k in (data.metric,)}):
            reason = "best validation metric 이 유한하지 않습니다"
            coordinator.finish_trial(trial_id, TrialStatus.FAILED, metrics={}, reason=reason)
            return base_result("FAILED", epochs_completed=outcome.epochs_completed, reason=reason)
        artifact_hashes = {
            _rel(best_path, artifact_root_p): sha256_file(best_path),
            _rel(best_path.with_suffix(".json"), artifact_root_p): sha256_file(
                best_path.with_suffix(".json")
            ),
        }
        latest_path = ckpt_dir / LATEST_NAME
        if latest_path.is_file():
            artifact_hashes[_rel(latest_path, artifact_root_p)] = sha256_file(latest_path)
        record_metrics: dict[str, Any] = {
            k: v for k, v in val_metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        record_metrics.update(
            {
                "best_epoch": int(outcome.best_epoch or 0),
                "epochs_completed": int(outcome.epochs_completed),
                "attempts": int(settings.attempt_no),
                "early_stopped": bool(outcome.early_stopped),
                "confusion_matrix": val_metrics.get("confusion_matrix"),
            }
        )
        if record_metrics.get("confusion_matrix") is None:
            record_metrics.pop("confusion_matrix", None)
        coordinator.finish_trial(
            trial_id,
            TrialStatus.COMPLETED,
            metrics=record_metrics,
            artifact_hashes=artifact_hashes,
            reason="completed",
        )
        _emit(
            event_log,
            "trial_completed",
            trial_id=trial_id,
            best_epoch=outcome.best_epoch,
            best_validation=outcome.best_validation,
            epochs_completed=outcome.epochs_completed,
            attempts=settings.attempt_no,
        )
        return base_result(
            "COMPLETED",
            epochs_completed=outcome.epochs_completed,
            best_epoch=outcome.best_epoch,
            best_validation=outcome.best_validation,
            validation_metrics=val_metrics,
            threshold=float(threshold) if isinstance(threshold, (int, float)) else None,
            parameters=outcome.parameters,
            artifact_hashes=artifact_hashes,
            early_stopped=outcome.early_stopped,
            resumed_from_epoch=outcome.resumed_from,
            reason="completed",
        )


def _release_memory(device: str) -> None:
    """실패 attempt 뒤 메모리 정리. empty_cache 는 회복 시점에만 (매 배치 아님)."""
    gc.collect()
    torch = torch_module()
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _load_with_fallback(
    ckpt_dir: Path, config_hash: str, event_log: EventLog | None
) -> LoadedCheckpoint | None:
    """latest.pt → (손상 시) best.pt. 둘 다 없으면 None, 둘 다 손상이면 E_CHECKPOINT_CORRUPT."""
    latest = ckpt_dir / LATEST_NAME
    best = ckpt_dir / BEST_NAME
    if not latest.is_file() and not best.is_file():
        return None
    first_error: AgentError | None = None
    if latest.is_file():
        try:
            return load_checkpoint(latest, config_hash)
        except AgentError as exc:
            if exc.code != "E_CHECKPOINT_CORRUPT":
                raise
            first_error = exc
            _emit(event_log, "checkpoint_corrupt", path=str(latest), fallback=str(best), message=exc.message)
    if best.is_file():
        try:
            return load_checkpoint(best, config_hash)
        except AgentError as exc:
            if exc.code != "E_CHECKPOINT_CORRUPT":
                raise
            raise AgentError(
                "E_CHECKPOINT_CORRUPT",
                "latest.pt 와 best.pt 가 모두 손상되어 재개할 수 없습니다",
                details={
                    "latest": str(latest),
                    "best": str(best),
                    "latest_error": first_error.message if first_error else None,
                    "best_error": exc.message,
                },
            ) from exc
    assert first_error is not None
    raise first_error


def _run_attempt(
    *,
    candidate: MlpCandidate,
    data: TrainingData,
    budget: BudgetTracker,
    coordinator: Coordinator,
    ckpt_dir: Path,
    settings: _AttemptSettings,
    ctx: HookContext,
    device: str,
    resume: bool,
    config_hash: str,
    max_epochs: int,
    patience: int,
    seed: int,
    hooks: TrainerHooks,
    event_log: EventLog | None,
    metrics_path: Path | None,
    log_every: int,
    lease: Lease | None,
    lr_override: float | None,
) -> _EpochOutcome:
    torch = torch_module()
    _seed_all(seed)
    gen = torch.Generator()
    gen.manual_seed(seed)
    arch: MlpArch = arch_from_candidate(
        candidate, task_type=data.task_type, in_dim=data.in_dim, feature_names=data.feature_names
    )
    model = build_mlp(arch).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=settings.learning_rate, weight_decay=float(candidate.weight_decay)
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=int(max_epochs))
    scaler = torch.amp.GradScaler("cuda") if settings.use_amp else None
    criterion = make_loss(data.task_type)

    start_epoch = 0
    best_val: float | None = None
    best_epoch: int | None = None
    bad_epochs = 0
    history: list[dict[str, Any]] = []
    early_stopped = False
    resumed_from: int | None = None
    if resume:
        ck = _load_with_fallback(ckpt_dir, config_hash, event_log)
        if ck is not None:
            model.load_state_dict(ck.model_state, strict=True)
            if ck.optimizer_state is not None:
                optimizer.load_state_dict(ck.optimizer_state)
            if ck.scheduler_state is not None:
                scheduler.load_state_dict(ck.scheduler_state)
            if scaler is not None and ck.scaler_state:
                scaler.load_state_dict(ck.scaler_state)
            restore_rng(ck.rng, gen)
            start_epoch = int(ck.meta.next_epoch)
            best_val = ck.meta.best_validation
            best_epoch = ck.meta.best_epoch
            bad_epochs = int(ck.meta.extra.get("bad_epochs", 0))
            history = list(ck.meta.extra.get("history", []))
            early_stopped = bool(ck.meta.extra.get("early_stopped", False))
            resumed_from = start_epoch
            if lr_override is not None:
                for g in optimizer.param_groups:
                    g["lr"] = float(lr_override)
                    g["initial_lr"] = float(lr_override)
                scheduler.base_lrs = [float(lr_override) for _ in scheduler.base_lrs]
            _emit(
                event_log,
                "resumed_from_checkpoint",
                trial_id=ctx.trial_id,
                attempt_id=ctx.attempt_id,
                checkpoint=ck.path.name,
                next_epoch=start_epoch,
                best_validation=best_val,
                restored_rng=True,
            )

    n_params = count_parameters(model)
    X_t = torch.from_numpy(data.X_train)
    y_t = torch.from_numpy(data.model_targets(data.y_train))
    dataset = torch.utils.data.TensorDataset(X_t, y_t)
    n_train = int(X_t.shape[0])
    sizes, group_counts = _batch_layout(n_train, settings.microbatch, settings.accumulation)
    n_batches = len(sizes)

    def outcome(status: TrialStatusName | None, epochs_completed: int, reason: str = "") -> _EpochOutcome:
        return _EpochOutcome(
            status=status,
            epochs_completed=epochs_completed,
            best_epoch=best_epoch,
            best_validation=best_val,
            history=history,
            early_stopped=early_stopped,
            resumed_from=resumed_from,
            parameters=n_params,
            reason=reason,
        )

    if early_stopped or start_epoch >= max_epochs:
        return outcome(None, start_epoch)

    epoch = start_epoch
    last_epoch_seconds: float | None = None
    for epoch in range(start_epoch, max_epochs):
        if last_epoch_seconds is not None and not budget.can_start_new_work(last_epoch_seconds):
            return outcome("BUDGET_EXCEEDED", epoch, "다음 epoch 를 시작할 시간 예산이 없습니다")
        loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=settings.microbatch,
            shuffle=True,
            generator=gen,
            num_workers=0,
            drop_last=False,
        )
        model.train()
        t0 = time.monotonic()
        running = torch.zeros((), dtype=torch.float64)
        n_seen = 0
        steps = 0
        logged: list[float] = []
        optimizer.zero_grad(set_to_none=True)
        autocast = (
            torch.autocast(device_type="cuda", dtype=torch.float16) if settings.use_amp else nullcontext()
        )
        for b_idx, (xb, yb) in enumerate(loader):
            hooks.on_batch(ctx, epoch, b_idx)
            xb = xb.to(device, non_blocking=False)
            yb = yb.to(device, non_blocking=False)
            group_idx = b_idx // settings.accumulation
            group_n = group_counts[group_idx]
            with autocast:
                out = model(xb)
                check_output_target(out, yb, data.task_type)
                loss_sum = criterion(out.float(), yb)
            if hooks.inject_non_finite(ctx, epoch, b_idx):
                loss_sum = loss_sum * float("nan")
            if not bool(torch.isfinite(loss_sum)):
                raise NonFiniteError(
                    {
                        "where": "loss",
                        "epoch": epoch,
                        "batch": b_idx,
                        "value": str(loss_sum.detach().cpu().item()),
                        "learning_rate": float(optimizer.param_groups[0]["lr"]),
                        "amp": settings.use_amp,
                    }
                )
            loss = loss_sum / float(group_n)
            if scaler is not None:
                scaler.scale(loss).backward()
            else:
                loss.backward()
            running += loss_sum.detach().cpu().to(torch.float64)
            n_seen += int(xb.shape[0])
            last_in_group = ((b_idx + 1) % settings.accumulation == 0) or (b_idx == n_batches - 1)
            if last_in_group:
                if scaler is not None:
                    scaler.unscale_(optimizer)
                grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP_MAX_NORM)
                if not bool(torch.isfinite(grad_norm)):
                    raise NonFiniteError(
                        {
                            "where": "gradient",
                            "epoch": epoch,
                            "batch": b_idx,
                            "value": str(float(grad_norm.detach().cpu().item())),
                            "learning_rate": float(optimizer.param_groups[0]["lr"]),
                            "amp": settings.use_amp,
                        }
                    )
                if scaler is not None:
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    optimizer.step()
                optimizer.zero_grad(set_to_none=True)
                steps += 1
            if (b_idx + 1) % max(1, log_every) == 0:
                logged.append(round(float(running.item()) / max(1, n_seen), 6))  # item() 주기 제한
                if budget.deadline_reached():
                    return outcome(
                        "BUDGET_EXCEEDED", epoch, "epoch 진행 중 시간 예산 초과 (부분 epoch 는 버림)"
                    )
        scheduler.step()
        train_loss = float(running.item()) / max(1, n_seen)
        del running

        raw_val = predict_in_batches(model, data.X_val, task_type=data.task_type, device=device)
        val_metrics, threshold = validation_metrics(data, raw_val)
        monitor_val = val_metrics.get(data.metric)
        monitor = float(monitor_val) if isinstance(monitor_val, (int, float)) else float("nan")
        improved = is_better(monitor, best_val, data.direction)
        if improved:
            best_val, best_epoch, bad_epochs = monitor, epoch, 0
        else:
            bad_epochs += 1
        epoch_seconds = round(time.monotonic() - t0, 4)
        last_epoch_seconds = epoch_seconds
        lr_now = float(optimizer.param_groups[0]["lr"])
        entry = {
            "epoch": epoch,
            "train_loss": round(train_loss, 8),
            data.metric: monitor,
            "improved": improved,
            "lr": lr_now,
            "seconds": epoch_seconds,
            "attempt_no": settings.attempt_no,
        }
        history.append(entry)
        if len(history) > MAX_HISTORY_IN_CHECKPOINT:
            history = history[-MAX_HISTORY_IN_CHECKPOINT:]
        early_stopped = bad_epochs >= patience
        extra = {
            "bad_epochs": bad_epochs,
            "history": history,
            "learning_rate": settings.learning_rate,
            "microbatch": settings.microbatch,
            "accumulation": settings.accumulation,
            "attempt_no": settings.attempt_no,
            "seed": seed,
            "device": device,
            "amp": settings.use_amp,
            "threshold": threshold,
            "early_stopped": early_stopped,
            "arch": arch.model_dump(mode="json"),
            "target_inverse": data.target_inverse(),
        }
        rng = capture_rng(gen)
        ck_metrics = dict(val_metrics)
        if threshold is not None:
            ck_metrics["threshold"] = threshold
        save_checkpoint(
            ckpt_dir / LATEST_NAME,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            rng_state=rng,
            next_epoch=epoch + 1,
            best_validation=best_val,
            best_epoch=best_epoch,
            config_hash=config_hash,
            metrics=ck_metrics,
            extra=extra,
        )
        if improved:
            save_checkpoint(
                ckpt_dir / BEST_NAME,
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                rng_state=rng,
                next_epoch=epoch + 1,
                best_validation=best_val,
                best_epoch=best_epoch,
                config_hash=config_hash,
                metrics=ck_metrics,
                extra=extra,
            )
        _append_jsonl(
            metrics_path,
            {
                "ts": now_iso(),
                "run_id": ctx.run_id,
                "trial_id": ctx.trial_id,
                "candidate_id": candidate.candidate_id,
                "attempt_id": ctx.attempt_id,
                "attempt_no": settings.attempt_no,
                "epoch": epoch,
                "train_loss": round(train_loss, 8),
                "train_loss_samples": logged,
                "steps": steps,
                "validation": {
                    k: v
                    for k, v in val_metrics.items()
                    if isinstance(v, (int, float, str, bool)) or v is None
                },
                "improved": improved,
                "best_validation": best_val,
                "lr": lr_now,
                "microbatch": settings.microbatch,
                "accumulation": settings.accumulation,
                "seconds": epoch_seconds,
                "budget_elapsed": round(budget.elapsed(), 3),
            },
        )
        _emit(
            event_log,
            "epoch_completed",
            trial_id=ctx.trial_id,
            attempt_id=ctx.attempt_id,
            epoch=epoch,
            train_loss=round(train_loss, 8),
            monitor=monitor,
            improved=improved,
            seconds=epoch_seconds,
        )
        hooks.on_epoch_end(ctx, epoch, val_metrics)
        # ---- epoch 경계 검사 (heartbeat → 예산 → cancel → pause → early stop)
        if lease is not None:
            coordinator.heartbeat(lease)
        if budget.deadline_reached():
            return outcome("BUDGET_EXCEEDED", epoch + 1, "epoch 완료 후 시간 예산 초과")
        if coordinator.is_cancel_requested(ctx.run_id):
            return outcome("CANCELLED", epoch + 1, "cancel 요청 (epoch 경계)")
        if coordinator.is_pause_requested(ctx.run_id):
            return outcome("PAUSED", epoch + 1, "pause 요청 (epoch 경계)")
        if early_stopped:
            _emit(event_log, "early_stopped", trial_id=ctx.trial_id, epoch=epoch, patience=patience)
            return outcome(None, epoch + 1)
    return outcome(None, max_epochs)
