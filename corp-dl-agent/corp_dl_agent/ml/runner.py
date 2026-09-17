"""run_task: TaskSpec 한 건의 학습 run 전체 (검증 → 계획 → 기준 모델 + MLP 후보 → validation 선택 → test 1회 → export/보고).

run 폴더 (<data_root>/runs/<run_id>/):
  taskspec.json run_config.json environment.json manifest.json data_report.json split_manifest.json plan.json
  events.jsonl epoch_metrics.jsonl checkpoints/<trial_id>/{latest.pt,best.pt,meta.json} trials/<trial_id>/model.joblib
  preprocessor/ metrics.csv final_evaluation.json model_card.json model_card.md report_ko.md report_ko.html export/ summary.json

규칙:
- 상태는 Coordinator(SQLite) 로 기록: CREATED→VALIDATING→PLANNED→RUNNING→EVALUATING→COMPLETED, PAUSED/CANCELLED/FAILED/
  BLOCKED_*/BUDGET_EXCEEDED. pause/cancel 은 epoch 경계와 trial 경계에서 확인한다.
- 기준 모델 3종 + MLP 후보 N (예산 max_candidates) 을 validation metric 으로 비교해 선택한다 (기준 모델이 우수하면 기준 모델).
- test 는 선택 고정 후 1회. final_evaluation.json 이 있으면 다시 평가하지 않는다 (test 기반 재튜닝 금지).
- 동일 fingerprint(task/data/split/code/lock/model/seed) 의 완료 trial 은 산출물 hash 검증 뒤 SKIPPED 로 재사용한다.
- resume 는 run fingerprint(task/code/lock/seed) 와 config hash(device/amp/epochs/patience/후보 수) 가 같을 때만.
  split manifest 의 data/taskspec/code/lock hash 가 다르면 E_FINGERPRINT_CHANGED (자동 재개 금지).
- acceptance 가 없으면 NEEDS_ACCEPTANCE_CRITERIA (값을 만들지 않는다). synthetic 표시는 모든 산출물에 남긴다.
- reporting 모듈(html/model_card) 은 있으면 사용하고 없으면 최소 report_ko.md 를 직접 쓴다.
"""

from __future__ import annotations

import csv
import importlib
import math
import platform
import secrets
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from corp_dl_agent.common import (
    StrictModel,
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    now_iso,
    read_json,
    sha256_file,
    sha256_text,
    validate_strict,
)
from corp_dl_agent.config.schemas import AppConfig
from corp_dl_agent.errors import AgentError, blocked_dependency
from corp_dl_agent.logs import EventLog
from corp_dl_agent.ml import compute_code_hash, normalize_lock_hash
from corp_dl_agent.ml.baselines import baseline_models, baseline_params, fit_baseline, predict_baseline
from corp_dl_agent.ml.checkpoint import load_checkpoint
from corp_dl_agent.ml.data import DataReport, load_dataset
from corp_dl_agent.ml.evaluation import (
    acceptance_status,
    evaluate_classification,
    evaluate_regression,
    is_finite_metrics,
    select_best,
)
from corp_dl_agent.ml.export import (
    MANIFEST_NAME,
    export_bundle,
    read_manifest,
    verify_bundle_files,
)
from corp_dl_agent.ml.mlp import MlpArch, build_mlp, torch_module
from corp_dl_agent.ml.preprocessing import FittedPreprocessor, check_leakage, fit_preprocessor
from corp_dl_agent.ml.registry import MlpCandidate, demo_candidates
from corp_dl_agent.ml.split import (
    SplitManifest,
    apply_split,
    group_split,
    load_split,
    save_split,
    verify_manifest_hashes,
)
from corp_dl_agent.ml.taskspec import NEEDS_ACCEPTANCE_CRITERIA, TaskSpec, taskspec_from_dict, taskspec_hash
from corp_dl_agent.ml.trainer import (
    SimulatedCrash,
    TrainerHooks,
    TrainingData,
    predict_in_batches,
    resolve_device,
    train_candidate,
    validation_metrics,
)
from corp_dl_agent.security.paths import resolve_within
from corp_dl_agent.state.budget import BudgetTracker, ResourceBudget
from corp_dl_agent.state.coordinator import (
    Coordinator,
    Lease,
    TrialRecord,
    compute_fingerprint,
    make_worker_id,
)
from corp_dl_agent.state.db import StateDB
from corp_dl_agent.state.machine import RESUMABLE_STATUSES, TERMINAL_STATUSES, RunStatus, TrialStatus
from corp_dl_agent.version import SCHEMA_VERSION, __version__
from corp_dl_agent.workspace import Workspace

RUN_MANIFEST_FORMAT = "corp-dl-agent/run-manifest/1"
SUMMARY_NAME = "summary.json"
TASKSPEC_NAME = "taskspec.json"
RUN_CONFIG_NAME = "run_config.json"
FINAL_EVAL_NAME = "final_evaluation.json"
DEFAULT_LEASE_TTL = 120.0
InterruptStatus = Literal["PAUSED", "CANCELLED", "BUDGET_EXCEEDED"]

_PROCESS_WORKER_ID: str | None = None


def process_worker_id() -> str:
    """프로세스당 하나의 worker id (같은 프로세스에서 재개하면 lease 를 갱신한다)."""
    global _PROCESS_WORKER_ID
    if _PROCESS_WORKER_ID is None:
        _PROCESS_WORKER_ID = make_worker_id()
    return _PROCESS_WORKER_ID


def make_run_id(task_id: str) -> str:
    return f"{task_id}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def _np() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise blocked_dependency("numpy", "학습 run") from exc
    return np


def _try_import(module: str, attr: str) -> Callable[..., Any] | None:
    """reporting 등 동시 작성 중인 모듈을 lazy 로 가져온다. 없거나 깨져 있으면 None."""
    try:
        mod = importlib.import_module(module)
        fn = getattr(mod, attr, None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 - 다른 소유자 모듈의 import 실패는 기능 저하로만 처리
        return None


# ---------------------------------------------------------------------------- 요약 모델
class CandidateSummary(StrictModel):
    name: str
    kind: Literal["sklearn", "mlp"]
    trial_id: str
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    epochs: int | None = None
    attempts: int = 0
    reused_from: str | None = None
    reason: str = ""


class RunSummary(StrictModel):
    schema_version: str = SCHEMA_VERSION
    app_version: str = __version__
    run_id: str
    task_id: str
    task_type: str
    status: str
    data_origin: str
    synthetic: bool
    run_dir: str
    purpose: str = ""
    task: dict[str, Any] = Field(default_factory=dict)
    data_report: dict[str, Any] = Field(default_factory=dict)
    split: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CandidateSummary] = Field(default_factory=list)
    selected: str | None = None
    selected_kind: str | None = None
    selected_trial_id: str | None = None
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    final_evaluation: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    threshold: float | None = None
    train_ranges: dict[str, dict[str, float]] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    hashes: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    trials_skipped: list[str] = Field(default_factory=list)
    resumed: bool = False
    test_locked: bool = False
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    generated_at: str = ""


def load_summary(run_dir: str | Path) -> RunSummary | None:
    p = Path(run_dir) / SUMMARY_NAME
    if not p.is_file():
        return None
    try:
        return validate_strict(RunSummary, read_json(p))
    except (ValueError, ValidationError, OSError):
        return None


def load_run_taskspec(run_dir: str | Path) -> TaskSpec:
    p = Path(run_dir) / TASKSPEC_NAME
    if not p.is_file():
        raise AgentError("E_INPUT_INVALID", f"run 폴더에 taskspec.json 이 없습니다: {Path(run_dir).name}")
    return taskspec_from_dict(read_json(p), source=str(p))


# ---------------------------------------------------------------------------- 예산/설정 hash
def resolve_budget(
    spec: TaskSpec, cfg: AppConfig, override: ResourceBudget | BudgetTracker | dict[str, Any] | None = None
) -> BudgetTracker:
    if isinstance(override, BudgetTracker):
        return override
    if isinstance(override, ResourceBudget):
        return BudgetTracker(override, label=spec.task_id)
    if isinstance(override, dict):
        try:
            return BudgetTracker(validate_strict(ResourceBudget, override), label=spec.task_id)
        except ValidationError as exc:
            raise AgentError(
                "E_SCHEMA_INVALID", "budget_override 가 올바르지 않습니다", details={"error": str(exc)[:300]}
            ) from exc
    rb = spec.resource_budget
    if rb.mode == "custom":
        budget = ResourceBudget(
            wall_time_seconds=int(rb.wall_time_seconds or 0),
            max_candidates=int(rb.max_candidates or 0),
            max_epochs=int(rb.max_epochs or 0),
            patience=int(rb.patience or 0),
            max_calls=0 if not cfg.network_allowed() else cfg.gateway.max_calls,
            max_tokens=0 if not cfg.network_allowed() else cfg.gateway.max_total_tokens,
            mode="custom",
        )
    else:
        budget = ResourceBudget.from_app_config(cfg, rb.mode)
        updates = {k: v for k, v in rb.model_dump().items() if k != "mode" and v is not None}
        if updates:
            budget = budget.model_copy(update=updates)
    return BudgetTracker(budget, label=spec.task_id)


def run_config_hash(cfg: AppConfig, budget: ResourceBudget) -> str:
    """재개 허용 여부를 정하는 설정 hash. 벽시계 예산(wall_time) 은 새 예산으로 재개할 수 있으므로 제외한다."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "device": cfg.ml.device,
        "amp": cfg.ml.amp,
        "num_workers": cfg.ml.num_workers,
        "max_epochs": budget.max_epochs,
        "patience": budget.patience,
        "max_candidates": budget.max_candidates,
        "oom_retries": cfg.ml.oom_retries,
        "nan_retries": cfg.ml.nan_retries,
    }
    return sha256_text(canonical_json(payload))


def run_fingerprint(spec: TaskSpec, *, code_hash: str, lock_hash: str) -> str:
    """run 수준 fingerprint: task/code/lock/seed (data/split hash 는 split manifest 와 trial fingerprint 가 담는다)."""
    return compute_fingerprint(
        task=taskspec_hash(spec),
        data_hash="run-level",
        split_hash="run-level",
        code_hash=code_hash,
        lock_hash=lock_hash,
        model_config={"level": "run"},
        seed=spec.seed,
    )


def environment_snapshot(
    cfg: AppConfig, *, device: str, amp: bool, seed: int, code_hash: str, worker_id: str
) -> dict[str, Any]:
    torch = torch_module("학습 run")
    versions: dict[str, str] = {"torch": str(torch.__version__)}
    for name in ("numpy", "pandas", "sklearn"):
        try:
            versions[name] = str(importlib.import_module(name).__version__)
        except Exception:  # noqa: BLE001
            versions[name] = "unknown"
    return {
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "device_requested": cfg.ml.device,
        "device": device,
        "amp_requested": cfg.ml.amp,
        "amp": amp,
        "cuda_available": bool(torch.cuda.is_available()),
        "torch_threads": int(torch.get_num_threads()),
        "num_workers": 0,
        "seed": seed,
        "profile": cfg.profile.value,
        "versions": versions,
        "code_hash": code_hash,
        "worker_id": worker_id,
        "bitwise_reproducibility": "다른 플랫폼/버전에서 비트 단위 동일성은 보장하지 않는다",
        "created_at": now_iso(),
    }


# ---------------------------------------------------------------------------- runner
class _Result:
    """후보(기준 모델/MLP) 하나의 학습 결과 (선택/평가/export 에 필요한 것만)."""

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        trial_id: str,
        status: str,
        metrics: dict[str, Any],
        artifact_hashes: dict[str, str],
        model: Any = None,
        epochs: int | None = None,
        attempts: int = 0,
        reused_from: str | None = None,
        reason: str = "",
        candidate: MlpCandidate | None = None,
        fingerprint: str = "",
    ) -> None:
        self.name = name
        self.kind = kind
        self.trial_id = trial_id
        self.status = status
        self.metrics = metrics
        self.artifact_hashes = artifact_hashes
        self.model = model
        self.epochs = epochs
        self.attempts = attempts
        self.reused_from = reused_from
        self.reason = reason
        self.candidate = candidate
        self.fingerprint = fingerprint

    def summary(self) -> CandidateSummary:
        return CandidateSummary(
            name=self.name,
            kind=self.kind,  # type: ignore[arg-type]
            trial_id=self.trial_id,
            status=self.status,
            metrics={k: v for k, v in self.metrics.items() if not isinstance(v, dict)},
            epochs=self.epochs,
            attempts=self.attempts,
            reused_from=self.reused_from,
            reason=self.reason,
        )


class TaskRunner:
    def __init__(
        self,
        spec: TaskSpec,
        cfg: AppConfig,
        ws: Workspace,
        *,
        run_id: str | None = None,
        resume: bool = False,
        budget_override: ResourceBudget | BudgetTracker | dict[str, Any] | None = None,
        lock_hash: str | None = None,
        hooks: TrainerHooks | None = None,
        worker_id: str | None = None,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL,
    ) -> None:
        if resume and not run_id:
            raise AgentError("E_USAGE", "resume 에는 --run-id 가 필요합니다")
        self.spec = spec
        self.cfg = cfg
        self.ws = ws
        self.run_id = run_id or make_run_id(spec.task_id)
        self.resume = resume
        self.hooks = hooks
        self.worker_id = worker_id or process_worker_id()
        self.lease_ttl = float(lease_ttl_seconds)
        self.lock_hash = normalize_lock_hash(lock_hash)
        self.budget = resolve_budget(spec, cfg, budget_override)
        self.run_dir = ws.run_dir(self.run_id)
        self.db: StateDB | None = None
        self.coordinator: Coordinator | None = None
        self.lease: Lease | None = None
        self.event_log: EventLog | None = None
        self.code_hash = compute_code_hash()
        self.fingerprint = run_fingerprint(spec, code_hash=self.code_hash, lock_hash=self.lock_hash)
        self.config_hash = run_config_hash(cfg, self.budget.budget)
        self.device, self.amp, self.warnings = resolve_device(cfg.ml.device, amp=cfg.ml.amp)
        self.results: dict[str, _Result] = {}
        self.trials_skipped: list[str] = []
        self.resumed = False
        self.locked_summary: RunSummary | None = None
        self._crashed = False
        # 검증 단계 산출
        self.df: Any = None
        self.report: DataReport | None = None
        self.split: SplitManifest | None = None
        self.fp: FittedPreprocessor | None = None
        self.data: TrainingData | None = None
        self.X_test: Any = None
        self.y_test: Any = None
        self.train_ranges: dict[str, dict[str, float]] = {}
        self.categories: dict[str, list[str]] = {}
        self.candidates: list[MlpCandidate] = []
        self.environment: dict[str, Any] = {}
        self.created_at = now_iso()

    # ------------------------------------------------------------------ 편의
    @property
    def coord(self) -> Coordinator:
        assert self.coordinator is not None
        return self.coordinator

    def _emit(self, event: str, **fields: Any) -> None:
        if self.event_log is not None:
            self.event_log.emit(event, run_id=self.run_id, **fields)

    def _status(self) -> RunStatus:
        return self.coord.get_run(self.run_id).status

    def _set_status(self, new: RunStatus, reason: str = "") -> None:
        cur = self._status()
        if cur is new:
            return
        self.coord.set_status(self.run_id, new, reason)
        self._emit("status_changed", **{"from": cur.value, "to": new.value, "reason": reason})

    def _check_requests(self) -> InterruptStatus | None:
        run = self.coord.get_run(self.run_id)
        if run.cancel_requested:
            return "CANCELLED"
        if run.pause_requested:
            return "PAUSED"
        if not self.budget.check(raise_on_exceed=False):
            return "BUDGET_EXCEEDED"
        return None

    def _artifact_root(self) -> Path:
        return self.ws.runs

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self._artifact_root().resolve()).as_posix()

    # ------------------------------------------------------------------ 진입
    def run(self) -> RunSummary:
        self._open()
        try:
            self._start_or_resume()
            if self.locked_summary is not None:
                return self.locked_summary
            self._validate()
            self._plan()
            interrupted = self._train()
            if interrupted is not None:
                return self._finish_interrupted(interrupted)
            self._evaluate()
            return self._complete()
        except SimulatedCrash:
            self._crashed = True  # 강제 종료 시뮬레이션: 상태/lease 를 정리하지 않는다
            raise
        except AgentError as exc:
            if exc.code == "E_BUDGET_EXCEEDED":
                return self._finish_interrupted("BUDGET_EXCEEDED", reason=exc.message)
            self._handle_error(exc)
            raise
        except Exception as exc:  # noqa: BLE001 - 예기치 않은 오류도 FAILED 로 기록한 뒤 전파
            self._handle_error(AgentError("E_INTERNAL", f"{type(exc).__name__}: {str(exc)[:300]}"))
            raise
        finally:
            self._close()

    def _open(self) -> None:
        self.ws.ensure()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.db = StateDB(self.ws.state_db)
        self.coordinator = Coordinator(self.db, self.worker_id)
        self.event_log = EventLog(self.run_dir / "events.jsonl")
        self.budget.start()

    def _close(self) -> None:
        if self.coordinator is not None and self.lease is not None and not self._crashed:
            try:
                self.coordinator.release(self.lease)
            except AgentError:
                pass
        if self.db is not None:
            self.db.close()
            self.db = None

    def _handle_error(self, exc: AgentError) -> None:
        if self.coordinator is None:
            return
        try:
            run = self.coord.get_run(self.run_id)
        except AgentError:
            return
        if exc.code in ("E_LEASE_HELD", "E_FINGERPRINT_CHANGED"):
            self._emit("run_error", code=exc.code, message=exc.message, state_unchanged=True)
            return
        target = {
            "E_BLOCKED_DEPENDENCY": RunStatus.BLOCKED_DEPENDENCY,
            "E_CONFIG_INVALID": RunStatus.BLOCKED_CONFIG,
            "E_CONFIG_UNKNOWN_KEY": RunStatus.BLOCKED_CONFIG,
            "E_BLOCKED_CONFIG": RunStatus.BLOCKED_CONFIG,
            "E_CONFIG_SECRET_MISSING": RunStatus.BLOCKED_CONFIG,
        }.get(exc.code, RunStatus.FAILED)
        if run.status in TERMINAL_STATUSES:
            return
        try:
            self.coord.set_status(self.run_id, target, reason=f"{exc.code}: {exc.message}"[:500])
        except AgentError:
            pass
        self._emit("run_error", code=exc.code, message=exc.message, status=target.value)
        try:
            self.coord.cleanup_own(self.run_id)
        except AgentError:
            pass
        self.lease = None
        self._write_summary(target.value, message=f"{exc.code}: {exc.message}")

    # ------------------------------------------------------------------ 시작/재개
    def _start_or_resume(self) -> None:
        if self.resume:
            try:
                run = self.coord.get_run(self.run_id)
            except AgentError as exc:
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"재개할 run 을 찾을 수 없습니다: {self.run_id}",
                    hint="status 명령으로 run_id 를 확인하세요.",
                    details={"run_id": self.run_id},
                ) from exc
            self.coord.assert_fingerprint_matches(self.run_id, self.fingerprint, self.config_hash)
            if run.status is RunStatus.COMPLETED:
                summary = load_summary(self.run_dir)
                if summary is None:
                    raise AgentError(
                        "E_INPUT_INVALID", f"완료된 run 의 summary.json 이 없습니다: {self.run_id}"
                    )
                summary = summary.model_copy(
                    update={"test_locked": True, "message": "이미 완료된 run 입니다 (test 재평가 없음)"}
                )
                self.locked_summary = summary
                self._emit("resume_noop_completed")
                return
            if run.status in TERMINAL_STATUSES:
                raise AgentError(
                    "E_STATE_TRANSITION",
                    f"종료된 run 은 재개할 수 없습니다: {run.status.value}",
                    details={"run_id": self.run_id, "status": run.status.value},
                )
            self.coord.cleanup_own(self.run_id)
            self.lease = self.coord.acquire_lease(self.run_id, self.lease_ttl)
            if run.status in RESUMABLE_STATUSES:
                self.coord.resume(self.run_id, reason="resume 요청")
            elif run.status in (RunStatus.BLOCKED_CONFIG, RunStatus.BLOCKED_DEPENDENCY):
                self.coord.set_status(self.run_id, RunStatus.VALIDATING, reason="차단 원인 해소 후 재검증")
            self.coord.clear_pause_request(self.run_id)
            self.resumed = True
            stored = load_summary(self.run_dir)
            if stored is not None:
                self.created_at = stored.created_at or self.created_at
                self.trials_skipped = list(stored.trials_skipped)
            self._emit("run_resumed", status=self._status().value, worker_id=self.worker_id)
        else:
            try:
                self.coord.create_run(
                    self.run_id,
                    self.fingerprint,
                    self.config_hash,
                    self.spec.task_type,
                    metadata={
                        "task_id": self.spec.task_id,
                        "data_origin": self.spec.data_origin,
                        "synthetic": self.spec.data_origin == "synthetic",
                        "run_dir": str(self.run_dir),
                        "lock_hash": self.lock_hash,
                        "budget_mode": self.budget.budget.mode,
                    },
                )
            except AgentError as exc:
                if exc.code == "E_INPUT_INVALID":
                    raise AgentError(
                        "E_INPUT_INVALID",
                        f"이미 존재하는 run_id 입니다: {self.run_id}",
                        hint="이어서 하려면 resume --run-id 를, 새로 하려면 다른 run_id 를 사용하세요.",
                        details={"run_id": self.run_id},
                    ) from exc
                raise
            self.lease = self.coord.acquire_lease(self.run_id, self.lease_ttl)
            atomic_write_json(self.run_dir / TASKSPEC_NAME, self.spec.model_dump(mode="json"))
            self._emit(
                "run_created", worker_id=self.worker_id, synthetic=self.spec.data_origin == "synthetic"
            )
        atomic_write_json(
            self.run_dir / RUN_CONFIG_NAME,
            {
                "run_id": self.run_id,
                "task_id": self.spec.task_id,
                "fingerprint": self.fingerprint,
                "config_hash": self.config_hash,
                "code_hash": self.code_hash,
                "lock_hash": self.lock_hash,
                "device": self.device,
                "amp": self.amp,
                "budget": self.budget.budget.model_dump(mode="json"),
                "ml": self.cfg.ml.model_dump(mode="json"),
                "profile": self.cfg.profile.value,
                "resumed": self.resumed,
                "updated_at": now_iso(),
            },
        )
        for w in self.warnings:
            self._emit("warning", message=w)

    # ------------------------------------------------------------------ 검증
    def _validate(self) -> None:
        if self._status() is RunStatus.CREATED:
            self._set_status(RunStatus.VALIDATING, "데이터 검증 시작")
        roots = [
            Path.cwd().resolve(),
            self.ws.data_root,
            self.ws.company_root,
            Path(self.spec.data_path).expanduser().resolve().parent,
        ]
        roots += [Path(p).expanduser().resolve() for p in self.cfg.paths.input_roots]
        roots += [Path(p).expanduser().resolve() for p in self.cfg.paths.sources_roots]
        try:
            self.df, self.report = load_dataset(
                self.spec, roots=roots, forbid_links=self.cfg.security.forbid_symlinks
            )
        except AgentError as exc:
            rep = exc.details.pop("report", None)
            if isinstance(rep, dict):
                atomic_write_json(self.run_dir / "data_report.json", rep)
            raise
        atomic_write_json(self.run_dir / "data_report.json", self.report.model_dump(mode="json"))
        split_path = self.run_dir / "split_manifest.json"
        if split_path.is_file():
            manifest = load_split(split_path)
            mismatched = verify_manifest_hashes(
                manifest, data_hash=self.report.sha256, taskspec=self.spec, code_hash=self.code_hash
            )
            if manifest.hashes.get("lock") != self.lock_hash:
                mismatched.append("lock")
            if mismatched:
                raise AgentError(
                    "E_FINGERPRINT_CHANGED",
                    f"저장된 split manifest 의 hash 가 현재와 다릅니다: {mismatched}",
                    details={"mismatched": mismatched, "run_id": self.run_id},
                )
        else:
            manifest = group_split(
                self.df,
                self.spec,
                code_hash=self.code_hash,
                lock_hash=self.lock_hash,
                data_hash=self.report.sha256,
            )
            save_split(manifest, split_path)
        self.split = manifest
        train, val, test = apply_split(self.df, manifest)
        findings = check_leakage(train, self.spec)
        if findings:
            raise AgentError(
                "E_LEAKAGE",
                "ID 성격의 열이 feature 에 포함되어 있습니다: " + ", ".join(f.column for f in findings),
                details={"findings": [f.model_dump(mode="json") for f in findings]},
            )
        fp = fit_preprocessor(self.spec, train)
        fp.save(self.run_dir / "preprocessor")
        self.fp = fp
        X_train = fp.transform(train)
        X_val = fp.transform(val)
        self.X_test = fp.transform(test)
        np = _np()
        y_train = np.asarray(train[self.spec.target], dtype="float64")
        y_val = np.asarray(val[self.spec.target], dtype="float64")
        self.y_test = np.asarray(test[self.spec.target], dtype="float64")
        self.data = TrainingData(
            task_type=self.spec.task_type,
            X_train=X_train,
            y_train=y_train,
            X_val=X_val,
            y_val=y_val,
            feature_names=list(fp.feature_names),
            metric=self.spec.metric,
            direction=self.spec.direction,
            target_unit=self.spec.target_unit(),
        )
        self.train_ranges = {
            col: {
                "min": float(np.nanmin(np.asarray(train[col], dtype="float64"))),
                "max": float(np.nanmax(np.asarray(train[col], dtype="float64"))),
            }
            for col in self.spec.numeric_features
        }
        self.categories = {
            col: sorted(set(train[col].astype(str).tolist())) for col in self.spec.categorical_features
        }
        self.environment = environment_snapshot(
            self.cfg,
            device=self.device,
            amp=self.amp,
            seed=self.spec.seed,
            code_hash=self.code_hash,
            worker_id=self.worker_id,
        )
        atomic_write_json(self.run_dir / "environment.json", self.environment)
        self._emit(
            "validated",
            n_rows=self.report.n_rows,
            n_features=fp.n_features,
            split=manifest.n_rows,
            encoding=self.report.encoding_detected,
        )
        if self._status() is RunStatus.VALIDATING:
            self._set_status(RunStatus.PLANNED, "검증 완료")
        self._write_summary(self._status().value)

    # ------------------------------------------------------------------ 계획
    def _plan(self) -> None:
        assert self.data is not None and self.split is not None
        b = self.budget.budget
        self.candidates = demo_candidates(self.spec.task_type, self.spec.seed, b.max_candidates)
        baselines = baseline_models(self.spec.task_type, seed=self.spec.seed)
        plan = {
            "run_id": self.run_id,
            "task_id": self.spec.task_id,
            "task_type": self.spec.task_type,
            "metric": self.spec.metric,
            "direction": self.spec.direction,
            "seed": self.spec.seed,
            "device": self.device,
            "amp": self.amp,
            "budget": self.budget.snapshot(),
            "baselines": [
                {
                    "name": name,
                    "kind": "sklearn",
                    "params": baseline_params(est),
                    "trial_id": self._baseline_trial_id(name),
                }
                for name, est in baselines.items()
            ],
            "mlp_candidates": [
                {
                    **c.model_dump(mode="json"),
                    "trial_id": self._mlp_trial_id(c),
                    "config_hash": c.config_hash(),
                }
                for c in self.candidates
            ],
            "max_epochs": b.max_epochs,
            "patience": b.patience,
            "selection": {
                "rule": f"validation {self.spec.metric} ({self.spec.direction}) 최적 후보 선택; 기준 모델이 우수하면 기준 모델",
                "threshold": "분류는 validation 에서 F1 최대 임계값 선택",
                "test_policy": "선택 고정 후 test 1회 평가. final_evaluation.json 이 있으면 재평가하지 않음",
            },
            "fingerprint": self.fingerprint,
            "config_hash": self.config_hash,
            "hashes": dict(self.split.hashes),
            "data_origin": self.spec.data_origin,
            "synthetic": self.spec.data_origin == "synthetic",
            "acceptance": self.spec.acceptance.model_dump(mode="json")
            if self.spec.acceptance
            else NEEDS_ACCEPTANCE_CRITERIA,
            "created_at": now_iso(),
        }
        atomic_write_json(self.run_dir / "plan.json", plan)
        if self._status() is RunStatus.PLANNED:
            self._set_status(RunStatus.RUNNING, "학습 시작")

    def _baseline_trial_id(self, name: str) -> str:
        return f"{self.run_id}-baseline-{name}"

    def _mlp_trial_id(self, cand: MlpCandidate) -> str:
        return f"{self.run_id}-{cand.candidate_id}"

    def _trial_fingerprint(self, model_config: dict[str, Any]) -> str:
        assert self.split is not None
        return compute_fingerprint(
            task=taskspec_hash(self.spec),
            data_hash=self.split.hashes["data"],
            split_hash=self.split.hashes["split"],
            code_hash=self.code_hash,
            lock_hash=self.lock_hash,
            model_config=model_config,
            seed=self.spec.seed,
        )

    # ------------------------------------------------------------------ 학습
    def _train(self) -> InterruptStatus | None:
        assert self.data is not None
        for name, est in baseline_models(self.spec.task_type, seed=self.spec.seed).items():
            req = self._check_requests()
            if req is not None:
                return req
            self._run_baseline(name, est)
            self._write_metrics_csv()
        for cand in self.candidates:
            req = self._check_requests()
            if req is not None:
                return req
            status = self._run_mlp(cand)
            self._write_metrics_csv()
            if status is not None:
                return status
        self._write_summary(self._status().value)
        return None

    def _existing_trial(self, trial_id: str) -> TrialRecord | None:
        try:
            return self.coord.get_trial(trial_id)
        except AgentError as exc:
            if exc.code == "E_INPUT_INVALID":
                return None
            raise

    def _fresh_trial_id(self, base: str) -> str:
        """같은 run 안에서 종료(FAILED/CANCELLED/검증 실패) 된 trial 이 있으면 접미사를 붙여 새 trial_id 를 만든다."""
        tid = base
        n = 1
        while self._existing_trial(tid) is not None:
            n += 1
            tid = f"{base}-r{n}"
        return tid

    def _reuse_from_record(
        self,
        name: str,
        kind: str,
        cand: MlpCandidate | None,
        record: TrialRecord,
        *,
        reused_from: str | None,
        trial_id: str,
        fingerprint: str,
    ) -> _Result:
        metrics = {k: v for k, v in record.metrics.items() if k != "definitions"}
        res = _Result(
            name=name,
            kind=kind,
            trial_id=trial_id,
            status="SKIPPED" if reused_from else "COMPLETED",
            metrics=metrics,
            artifact_hashes=dict(record.artifact_hashes),
            epochs=int(metrics.get("epochs_completed") or 0) if kind == "mlp" else None,
            attempts=record.attempts,
            reused_from=reused_from,
            reason=f"reused:{reused_from}" if reused_from else "completed (이전 실행)",
            candidate=cand,
            fingerprint=fingerprint,
        )
        if reused_from:
            self.trials_skipped.append(trial_id)
        self._emit("trial_reused", trial_id=trial_id, reused_from=reused_from, name=name, kind=kind)
        return res

    def _try_reuse(
        self, name: str, kind: str, cand: MlpCandidate | None, base_trial_id: str, fingerprint: str
    ) -> tuple[_Result | None, str, TrialRecord | None]:
        """(재사용 결과 | None, 사용할 trial_id, 이 run 의 기존 trial 레코드(재개용) | None)."""
        existing = self._existing_trial(base_trial_id)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise AgentError(
                    "E_FINGERPRINT_CHANGED",
                    f"trial {base_trial_id} 의 fingerprint 가 현재와 다릅니다",
                    details={"trial_id": base_trial_id},
                )
            if existing.status in (TrialStatus.COMPLETED, TrialStatus.SKIPPED):
                checks = self.coord.verify_trial_artifacts(existing, self._artifact_root())
                if checks and all(v == "ok" for v in checks.values()):
                    src = existing.reason[len("reused:") :] if existing.reason.startswith("reused:") else None
                    return (
                        self._reuse_from_record(
                            name,
                            kind,
                            cand,
                            existing,
                            reused_from=src,
                            trial_id=base_trial_id,
                            fingerprint=fingerprint,
                        ),
                        base_trial_id,
                        None,
                    )
                self.warnings.append(f"trial {base_trial_id} 의 산출물 hash 검증 실패 → 다시 학습합니다")
                return None, self._fresh_trial_id(base_trial_id), None
            if existing.status in (TrialStatus.FAILED, TrialStatus.CANCELLED):
                return None, self._fresh_trial_id(base_trial_id), None
            return None, base_trial_id, existing  # PENDING/RUNNING/INTERRUPTED → 재개
        prior = self.coord.find_completed_trial(fingerprint)
        if prior is not None and self.coord.is_trial_reusable(prior, self._artifact_root()):
            self.coord.create_trial(
                self.run_id,
                base_trial_id,
                cand.model_dump(mode="json") if cand else {"kind": kind, "name": name},
                fingerprint,
            )
            rec = self.coord.mark_trial_skipped(base_trial_id, prior)
            return (
                self._reuse_from_record(
                    name,
                    kind,
                    cand,
                    rec,
                    reused_from=prior.trial_id,
                    trial_id=base_trial_id,
                    fingerprint=fingerprint,
                ),
                base_trial_id,
                None,
            )
        return None, base_trial_id, None

    def _run_baseline(self, name: str, est: Any) -> None:
        assert self.data is not None
        fingerprint = self._trial_fingerprint(
            {"kind": "sklearn", "name": name, "params": baseline_params(est)}
        )
        reused, trial_id, existing = self._try_reuse(
            name, "sklearn", None, self._baseline_trial_id(name), fingerprint
        )
        if reused is not None:
            self.results[name] = reused
            return
        if existing is None:
            self.coord.create_trial(
                self.run_id,
                trial_id,
                {"kind": "sklearn", "name": name, "params": baseline_params(est)},
                fingerprint,
            )
        attempt_id = self.coord.create_attempt(trial_id)
        self._emit("baseline_started", trial_id=trial_id, name=name)
        try:
            fit_baseline(name, est, self.data.X_train, self.data.y_train, task_type=self.spec.task_type)
            raw = predict_baseline(est, self.data.X_val, self.spec.task_type)
            if self.spec.task_type == "regression":
                metrics, threshold = validation_metrics(
                    self.data,
                    (raw - float(self.data.target_mean or 0.0)) / float(self.data.target_std or 1.0),
                )
            else:
                metrics, threshold = validation_metrics(self.data, raw)
        except AgentError as exc:
            self.coord.finish_trial(
                trial_id, TrialStatus.FAILED, metrics={}, reason=f"{exc.code}: {exc.message}"[:300]
            )
            self.warnings.append(f"기준 모델 {name} 학습 실패: {exc.code}")
            self.results[name] = _Result(
                name=name,
                kind="sklearn",
                trial_id=trial_id,
                status="FAILED",
                metrics={},
                artifact_hashes={},
                reason=exc.message,
                fingerprint=fingerprint,
            )
            return
        trial_dir = self.run_dir / "trials" / trial_id
        trial_dir.mkdir(parents=True, exist_ok=True)
        import io

        try:
            import joblib
        except ImportError as exc:  # pragma: no cover
            raise blocked_dependency("joblib", "기준 모델 저장") from exc
        from corp_dl_agent.common import atomic_write_bytes

        buf = io.BytesIO()
        joblib.dump(est, buf)
        model_path = trial_dir / "model.joblib"
        atomic_write_bytes(model_path, buf.getvalue())
        record_metrics: dict[str, Any] = {
            k: v for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if threshold is not None:
            record_metrics["threshold"] = float(threshold)
        if isinstance(metrics.get("confusion_matrix"), dict):
            record_metrics["confusion_matrix"] = metrics["confusion_matrix"]
        record_metrics["attempts"] = 1
        artifact_hashes = {self._rel(model_path): sha256_file(model_path)}
        if not is_finite_metrics({self.spec.metric: record_metrics.get(self.spec.metric)}):
            self.coord.finish_trial(
                trial_id, TrialStatus.FAILED, metrics={}, reason="validation metric 비유한"
            )
            self.warnings.append(f"기준 모델 {name} 의 validation {self.spec.metric} 이 유한하지 않습니다")
            self.results[name] = _Result(
                name=name,
                kind="sklearn",
                trial_id=trial_id,
                status="FAILED",
                metrics={},
                artifact_hashes={},
                reason="non-finite metric",
                fingerprint=fingerprint,
            )
            return
        self.coord.finish_trial(
            trial_id,
            TrialStatus.COMPLETED,
            metrics=record_metrics,
            artifact_hashes=artifact_hashes,
            reason="completed",
        )
        self._emit(
            "baseline_completed",
            trial_id=trial_id,
            name=name,
            attempt_id=attempt_id,
            metric=record_metrics.get(self.spec.metric),
        )
        self.results[name] = _Result(
            name=name,
            kind="sklearn",
            trial_id=trial_id,
            status="COMPLETED",
            metrics=record_metrics,
            artifact_hashes=artifact_hashes,
            model=est,
            attempts=1,
            reason="completed",
            fingerprint=fingerprint,
        )
        self.budget.check()

    def _run_mlp(self, cand: MlpCandidate) -> InterruptStatus | None:
        assert self.data is not None
        b = self.budget.budget
        model_config = {**cand.model_dump(mode="json"), "max_epochs": b.max_epochs, "patience": b.patience}
        fingerprint = self._trial_fingerprint(model_config)
        reused, trial_id, existing = self._try_reuse(
            cand.candidate_id, "mlp", cand, self._mlp_trial_id(cand), fingerprint
        )
        if reused is not None:
            self.results[cand.candidate_id] = reused
            return None
        self.budget.record_candidate_start()
        result = train_candidate(
            cand,
            self.data,
            self.budget,
            self.coord,
            self.run_dir,
            device=self.device,
            resume=self.resumed or existing is not None,
            run_id=self.run_id,
            fingerprint=fingerprint,
            trial_id=trial_id,
            max_epochs=b.max_epochs,
            patience=b.patience,
            amp=self.amp,
            seed=self.spec.seed,
            hooks=self.hooks,
            event_log=self.event_log,
            epoch_metrics_path=self.run_dir / "epoch_metrics.jsonl",
            artifact_root=self._artifact_root(),
            oom_retries=self.cfg.ml.oom_retries,
            nan_retries=self.cfg.ml.nan_retries,
            lease=self.lease,
        )
        metrics = {k: v for k, v in result.validation_metrics.items() if k != "definitions"}
        metrics.update(
            {
                "best_epoch": result.best_epoch,
                "epochs_completed": result.epochs_completed,
                "attempts": result.attempts,
            }
        )
        self.results[cand.candidate_id] = _Result(
            name=cand.candidate_id,
            kind="mlp",
            trial_id=trial_id,
            status=result.status,
            metrics=metrics
            if result.status == "COMPLETED"
            else {"epochs_completed": result.epochs_completed, "attempts": result.attempts},
            artifact_hashes=dict(result.artifact_hashes),
            epochs=result.epochs_completed,
            attempts=result.attempts,
            reason=result.reason,
            candidate=cand,
            fingerprint=fingerprint,
        )
        if result.status == "FAILED":
            self.warnings.append(f"MLP 후보 {cand.candidate_id} 실패: {result.reason}")
            return None
        if result.status in ("PAUSED", "CANCELLED", "BUDGET_EXCEEDED"):
            if result.status == "PAUSED":
                self.lease = None  # cleanup_own 이 lease 를 해제했다
            elif result.status == "BUDGET_EXCEEDED":
                self.lease = None
            return result.status  # type: ignore[return-value]
        return None

    def _write_metrics_csv(self) -> None:
        keys: list[str] = []
        for r in self.results.values():
            for k, v in r.metrics.items():
                if k not in keys and not isinstance(v, (dict, list)):
                    keys.append(k)
        path = self.run_dir / "metrics.csv"
        tmp_rows = []
        for r in self.results.values():
            row = {
                "name": r.name,
                "kind": r.kind,
                "trial_id": r.trial_id,
                "status": r.status,
                "epochs": r.epochs,
                "attempts": r.attempts,
                "reused_from": r.reused_from or "",
            }
            for k in keys:
                v = r.metrics.get(k)
                row[k] = "" if v is None or isinstance(v, (dict, list)) else v
            tmp_rows.append(row)
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["name", "kind", "trial_id", "status", "epochs", "attempts", "reused_from", *keys],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in tmp_rows:
            writer.writerow(row)
        atomic_write_text(path, buf.getvalue())

    # ------------------------------------------------------------------ 평가
    def _materialize(self, res: _Result) -> tuple[Any, MlpArch | None, dict[str, Any] | None]:
        """선택 모델 객체를 준비한다: 메모리에 있으면 그대로, 아니면 검증된 산출물에서 로드."""
        if res.model is not None:
            return res.model, None, None
        root = self._artifact_root().resolve()
        if res.kind == "sklearn":
            key = next((k for k in res.artifact_hashes if k.endswith("model.joblib")), None)
            if key is None:
                raise AgentError(
                    "E_ARTIFACT_UNTRUSTED", f"trial {res.trial_id} 의 model.joblib 기록이 없습니다"
                )
            path = resolve_within(root / key, [root], forbid_links=self.cfg.security.forbid_symlinks)
            if not path.is_file() or sha256_file(path) != res.artifact_hashes[key]:
                raise AgentError(
                    "E_ARTIFACT_UNTRUSTED", f"trial {res.trial_id} 의 model.joblib checksum 이 다릅니다"
                )
            import joblib

            model = joblib.load(path)  # 자기 생성 + checksum 검증 뒤에만
            res.model = model
            return model, None, None
        key = next((k for k in res.artifact_hashes if k.endswith("best.pt")), None)
        if key is None:
            raise AgentError("E_ARTIFACT_UNTRUSTED", f"trial {res.trial_id} 의 best.pt 기록이 없습니다")
        path = resolve_within(root / key, [root], forbid_links=self.cfg.security.forbid_symlinks)
        if sha256_file(path) != res.artifact_hashes[key]:
            raise AgentError("E_ARTIFACT_UNTRUSTED", f"trial {res.trial_id} 의 best.pt checksum 이 다릅니다")
        ck = load_checkpoint(path, res.fingerprint or None)
        arch = validate_strict(MlpArch, ck.meta.extra.get("arch"))
        model = build_mlp(arch)
        model.load_state_dict(ck.model_state, strict=True)
        model.eval()
        res.model = model
        return model, arch, ck.meta.extra.get("target_inverse")

    def _test_predictions(self, res: _Result, model: Any) -> Any:
        assert self.data is not None
        if res.kind == "sklearn":
            return predict_baseline(model, self.X_test, self.spec.task_type)
        raw = predict_in_batches(model, self.X_test, task_type=self.spec.task_type, device="cpu")
        return self.data.inverse(raw)

    def _evaluate(self) -> None:
        assert self.data is not None and self.fp is not None and self.split is not None
        if self._status() is RunStatus.RUNNING:
            self._set_status(RunStatus.EVALUATING, "validation 기준 선택 및 test 1회 평가")
        usable = {
            n: r.metrics
            for n, r in self.results.items()
            if r.status in ("COMPLETED", "SKIPPED") and r.metrics
        }
        if not usable:
            raise AgentError(
                "E_INPUT_INVALID",
                "선택 가능한 후보(완료된 기준 모델/MLP) 가 없습니다",
                details={"warnings": self.warnings},
            )
        final_path = self.run_dir / FINAL_EVAL_NAME
        if final_path.is_file():
            final = read_json(final_path)
            selected = str(final.get("selected"))
            if selected not in self.results:
                raise AgentError(
                    "E_INPUT_INVALID",
                    f"final_evaluation.json 의 선택 모델 {selected} 이 현재 결과에 없습니다",
                )
            self._emit("final_evaluation_locked", selected=selected)
            self.test_locked = True
        else:
            selected, _ = select_best(usable, self.spec.metric, self.spec.direction)
            res = self.results[selected]
            model, _, _ = self._materialize(res)
            threshold = res.metrics.get("threshold")
            preds = self._test_predictions(res, model)
            if self.spec.task_type == "regression":
                test_metrics = evaluate_regression(self.y_test, preds, self.spec.target_unit())
            else:
                thr = float(threshold) if isinstance(threshold, (int, float)) else 0.5
                test_metrics = evaluate_classification(self.y_test.astype("int64"), preds, thr)
            acc = acceptance_status(self.spec.acceptance, test_metrics)
            final = {
                "run_id": self.run_id,
                "task_id": self.spec.task_id,
                "selected": selected,
                "selected_kind": res.kind,
                "selected_trial_id": res.trial_id,
                "selection_basis": f"validation {self.spec.metric} ({self.spec.direction})",
                "validation_metric": res.metrics.get(self.spec.metric),
                "threshold": threshold,
                "test_metrics": test_metrics,
                "n_test": int(self.split.n_rows["test"]),
                "acceptance": acc.model_dump(mode="json"),
                "data_origin": self.spec.data_origin,
                "synthetic": self.spec.data_origin == "synthetic",
                "hashes": dict(self.split.hashes),
                "locked": True,
                "note": "test 는 1회만 평가한다. 이 파일이 있으면 재실행해도 다시 평가하지 않는다",
                "evaluated_at": now_iso(),
            }
            atomic_write_json(final_path, final)
            self.test_locked = False
            self._emit(
                "final_evaluation_written",
                selected=selected,
                kind=res.kind,
                metric=test_metrics.get(self.spec.metric),
            )
        self.final = final
        res = self.results[str(final["selected"])]
        self._export(res)
        self._write_summary(self._status().value)
        self._write_model_card_and_report()
        self._write_run_manifest()

    def _export(self, res: _Result) -> None:
        assert self.fp is not None and self.data is not None
        export_dir = self.run_dir / "export"
        if (export_dir / MANIFEST_NAME).is_file():
            try:
                m = read_manifest(export_dir)
                verify_bundle_files(export_dir, m)
                if m.model_name == res.name:
                    self._emit("export_reused", dir=str(export_dir))
                    return
            except AgentError:
                pass
        model, arch, _ = self._materialize(res)
        if res.kind == "mlp" and arch is None:
            assert res.candidate is not None
            from corp_dl_agent.ml.mlp import arch_from_candidate

            arch = arch_from_candidate(
                res.candidate,
                task_type=self.spec.task_type,
                in_dim=self.fp.n_features,
                feature_names=list(self.fp.feature_names),
            )
        threshold = res.metrics.get("threshold")
        export_bundle(
            export_dir,
            model_kind=res.kind,  # type: ignore[arg-type]
            model=model,
            preprocessor_state=self.fp,
            input_schema={
                "numeric_features": list(self.spec.numeric_features),
                "categorical_features": list(self.spec.categorical_features),
                "feature_names": list(self.fp.feature_names),
                "n_features": self.fp.n_features,
                "categories": self.categories,
                "id_column": self.spec.id_column,
                "excluded_columns": list(self.spec.excluded_columns),
            },
            units=dict(self.spec.units),
            target_inverse=self.data.target_inverse()
            if res.kind == "mlp"
            else {"kind": "identity", "mean": None, "std": None, "unit": self.spec.target_unit()},
            threshold=float(threshold)
            if isinstance(threshold, (int, float))
            else (0.5 if self.spec.task_type == "binary_classification" else None),
            train_ranges=self.train_ranges,
            data_origin=self.spec.data_origin,
            purpose=self.spec.purpose,
            run_id=self.run_id,
            task_id=self.spec.task_id,
            task_type=self.spec.task_type,
            target=self.spec.target,
            id_column=self.spec.id_column,
            model_name=res.name,
            arch=arch,
            hashes=dict(self.split.hashes) if self.split else {},
        )
        self._emit("export_written", dir=str(export_dir), kind=res.kind, model=res.name)

    # ------------------------------------------------------------------ 요약/보고
    def _budget_snapshot(self) -> dict[str, Any]:
        return self.budget.snapshot()

    def _build_summary(self, status: str, *, message: str = "") -> RunSummary:
        final = getattr(self, "final", None) or {}
        test_metrics = {k: v for k, v in (final.get("test_metrics") or {}).items() if k != "definitions"}
        selected = final.get("selected")
        sel = self.results.get(str(selected)) if selected else None
        acceptance = final.get("acceptance") or (
            {
                "state": NEEDS_ACCEPTANCE_CRITERIA,
                "message": "업무 허용오차(acceptance)가 입력되지 않아 합격 여부를 판정하지 않습니다",
            }
            if self.spec.acceptance is None
            else {
                "state": "NOT_RUN",
                "metric": self.spec.acceptance.metric,
                "threshold": self.spec.acceptance.threshold,
                "direction": self.spec.acceptance.direction,
                "message": "test 평가 전",
            }
        )
        artifacts: dict[str, str] = {}
        for name in (
            "taskspec.json",
            "run_config.json",
            "environment.json",
            "manifest.json",
            "data_report.json",
            "split_manifest.json",
            "plan.json",
            "events.jsonl",
            "epoch_metrics.jsonl",
            "metrics.csv",
            FINAL_EVAL_NAME,
            "model_card.json",
            "model_card.md",
            "report_ko.md",
            "report_ko.html",
            "export/manifest.json",
            SUMMARY_NAME,
        ):
            p = self.run_dir / name
            if p.is_file():
                artifacts[name] = sha256_file(p) if name != SUMMARY_NAME else "self"
        data_report = {}
        if self.report is not None:
            data_report = {
                "path": Path(self.report.path).name,
                "sha256": self.report.sha256,
                "encoding_detected": self.report.encoding_detected,
                "n_rows": self.report.n_rows,
                "n_cols": self.report.n_cols,
                "n_groups": self.report.n_groups,
                "warnings": len([i for i in self.report.issues if i.level != "error"]),
            }
        split = {}
        if self.split is not None:
            split = {
                "policy": self.split.policy,
                "n_groups": self.split.n_groups,
                "n_rows": self.split.n_rows,
                "row_fractions": {k: round(v, 4) for k, v in self.split.row_fractions.items()},
                "overlap_checked": self.split.overlap_checked,
            }
        return RunSummary(
            run_id=self.run_id,
            task_id=self.spec.task_id,
            task_type=self.spec.task_type,
            status=status,
            data_origin=self.spec.data_origin,
            synthetic=self.spec.data_origin == "synthetic",
            run_dir=str(self.run_dir),
            purpose=self.spec.purpose,
            task=self.spec.model_dump(mode="json"),
            data_report=data_report,
            split=split,
            candidates=[r.summary() for r in self.results.values()],
            selected=str(selected) if selected else None,
            selected_kind=str(final.get("selected_kind")) if selected else None,
            selected_trial_id=str(final.get("selected_trial_id")) if selected else None,
            validation_metrics={
                k: v for k, v in (sel.metrics if sel else {}).items() if not isinstance(v, dict)
            },
            final_evaluation=test_metrics,
            acceptance=acceptance,
            threshold=float(final["threshold"]) if isinstance(final.get("threshold"), (int, float)) else None,
            train_ranges=self.train_ranges,
            budget=self._budget_snapshot(),
            hashes={
                **(dict(self.split.hashes) if self.split else {}),
                "fingerprint": self.fingerprint,
                "config_hash": self.config_hash,
            },
            environment={k: v for k, v in self.environment.items() if k not in ("bitwise_reproducibility",)},
            artifacts=artifacts,
            warnings=list(self.warnings),
            trials_skipped=list(self.trials_skipped),
            resumed=self.resumed,
            test_locked=bool(getattr(self, "test_locked", False)),
            message=message,
            created_at=self.created_at,
            updated_at=now_iso(),
            generated_at=now_iso(),
        )

    def _write_summary(self, status: str, *, message: str = "") -> RunSummary:
        summary = self._build_summary(status, message=message)
        atomic_write_json(self.run_dir / SUMMARY_NAME, summary.model_dump(mode="json"))
        self.summary = summary
        return summary

    def _write_model_card_and_report(self) -> None:
        summary = self._write_summary(self._status().value)
        d = summary.model_dump(mode="json")
        d["limitations"] = (
            ["MLP 후보의 재시도(OOM/NaN) 는 microbatch/LR 이 바뀐 새 attempt 이며 동일한 수학 실험이 아니다"]
            if any(r.attempts > 1 for r in self.results.values())
            else []
        )
        build_card = _try_import("corp_dl_agent.reporting.model_card", "build_model_card")
        write_card = _try_import("corp_dl_agent.reporting.model_card", "write_model_card")
        if build_card is not None and write_card is not None:
            try:
                card = build_card(d)
                write_card(card, self.run_dir / "model_card.json", self.run_dir / "model_card.md")
            except Exception as exc:  # noqa: BLE001 - reporting 실패는 run 실패가 아니다
                self.warnings.append(f"reporting.model_card 실패: {type(exc).__name__}")
                self._write_minimal_model_card(d)
        else:
            self._write_minimal_model_card(d)
        render = _try_import("corp_dl_agent.reporting.html", "render_report_ko")
        if render is not None:
            try:
                render(d, self.run_dir / "report_ko.html", self.run_dir / "report_ko.md")
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"reporting.html 실패: {type(exc).__name__}")
                self._write_minimal_report(d)
        else:
            self.warnings.append(
                "reporting.html 모듈이 없어 report_ko.html 은 NOT_RUN (report_ko.md 만 작성)"
            )
            self._write_minimal_report(d)

    def _write_minimal_model_card(self, d: dict[str, Any]) -> None:
        card = {
            "schema_version": SCHEMA_VERSION,
            "generator": f"corp-dl-agent {__version__} (runner minimal)",
            "created_at": now_iso(),
            "run_id": self.run_id,
            "task_id": self.spec.task_id,
            "task_type": self.spec.task_type,
            "target": self.spec.target,
            "unit": self.spec.target_unit(),
            "data_origin": self.spec.data_origin,
            "purpose": self.spec.purpose,
            "selected_model": d.get("selected"),
            "model_kind": d.get("selected_kind"),
            "features": {
                "numeric": list(self.spec.numeric_features),
                "categorical": list(self.spec.categorical_features),
                "excluded": list(self.spec.excluded_columns),
            },
            "train_ranges": self.train_ranges,
            "validation_metrics": d.get("validation_metrics"),
            "test_metrics": d.get("final_evaluation"),
            "acceptance": d.get("acceptance"),
            "threshold": d.get("threshold"),
            "hashes": d.get("hashes"),
            "production_auto_select": self.spec.data_origin == "corporate",
            "notices": ["합성(synthetic) 데이터로 학습한 모델은 운영 자동 선택에서 제외됩니다"]
            if self.spec.data_origin == "synthetic"
            else [],
        }
        atomic_write_json(self.run_dir / "model_card.json", card)
        lines = [f"# Model Card — {self.spec.task_id} ({self.run_id})", ""]
        for k in (
            "task_type",
            "target",
            "data_origin",
            "selected_model",
            "model_kind",
            "threshold",
            "production_auto_select",
        ):
            lines.append(f"- {k}: {card.get(k)}")
        lines += ["", "## test metrics (1회)"] + [
            f"- {k}: {v}" for k, v in (d.get("final_evaluation") or {}).items()
        ]
        lines += ["", "## acceptance", f"- {d.get('acceptance')}"]
        atomic_write_text(self.run_dir / "model_card.md", "\n".join(lines) + "\n")

    def _write_minimal_report(self, d: dict[str, Any]) -> None:
        lines = [
            f"# 학습 보고서 — {self.spec.task_id}",
            "",
            f"- run_id: `{self.run_id}` / 상태: {d.get('status')} / data_origin: {self.spec.data_origin}",
        ]
        if self.spec.data_origin == "synthetic":
            lines.append("- **합성 데이터 결과입니다. 업무 판단에 사용하지 마세요.**")
        for w in d.get("warnings") or []:
            lines.append(f"- 경고: {w}")
        lines += [
            "",
            "## 후보 비교 (validation)",
            "",
            "| 후보 | 종류 | 상태 | " + self.spec.metric + " |",
            "|---|---|---|---|",
        ]
        for c in d.get("candidates") or []:
            lines.append(
                f"| {c['name']} | {c['kind']} | {c['status']} | {(c.get('metrics') or {}).get(self.spec.metric)} |"
            )
        lines += [
            "",
            f"선택 모델: **{d.get('selected')}** (validation 기준 선택, test 는 1회 평가)",
            "",
            "## 최종 평가 (test, 1회)",
            "",
        ]
        lines += [f"- {k}: {v}" for k, v in (d.get("final_evaluation") or {}).items()]
        lines += ["", "## 업무 허용 기준", "", f"- {d.get('acceptance')}", "", "## 산출물", ""]
        lines += [f"- {k}: {v}" for k, v in (d.get("artifacts") or {}).items()]
        lines += ["", "(report_ko.html: reporting 모듈이 없어 생성하지 않음 — NOT_RUN)"]
        atomic_write_text(self.run_dir / "report_ko.md", "\n".join(lines) + "\n")

    def _write_run_manifest(self) -> None:
        files: dict[str, dict[str, Any]] = {}
        for p in sorted(self.run_dir.rglob("*")):
            if (
                not p.is_file()
                or p.name == "manifest.json"
                and p.parent == self.run_dir
                or p.suffix == ".tmp"
            ):
                continue
            rel = p.relative_to(self.run_dir).as_posix()
            files[rel] = {"sha256": sha256_file(p), "size": p.stat().st_size}
        atomic_write_json(
            self.run_dir / "manifest.json",
            {
                "format": RUN_MANIFEST_FORMAT,
                "created_by": "corp-dl-agent",
                "app_version": __version__,
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "task_id": self.spec.task_id,
                "status": self._status().value,
                "data_origin": self.spec.data_origin,
                "synthetic": self.spec.data_origin == "synthetic",
                "hashes": {
                    **(dict(self.split.hashes) if self.split else {}),
                    "fingerprint": self.fingerprint,
                    "config_hash": self.config_hash,
                },
                "files": files,
                "created_at": self.created_at,
                "updated_at": now_iso(),
            },
        )

    # ------------------------------------------------------------------ 종료
    def _finish_interrupted(self, status: InterruptStatus, *, reason: str = "") -> RunSummary:
        target = RunStatus(status)
        try:
            cur = self._status()
            if cur is not target and target in {
                RunStatus.PAUSED,
                RunStatus.CANCELLED,
                RunStatus.BUDGET_EXCEEDED,
            }:
                self.coord.set_status(self.run_id, target, reason or f"{status} (경계에서 중단)")
        except AgentError as exc:
            self.warnings.append(f"상태 전이 실패: {exc.code}")
        try:
            self.coord.cleanup_own(self.run_id)
        except AgentError:
            pass
        self.lease = None
        msg = {
            "PAUSED": "pause 요청으로 epoch/trial 경계에서 멈췄습니다. resume --run-id 로 이어서 실행하세요.",
            "CANCELLED": "cancel 요청으로 중단되었습니다.",
            "BUDGET_EXCEEDED": "자원 예산을 초과해 안전하게 종료했습니다. 예산은 완료 보증이 아닙니다. 새 예산으로 resume 할 수 있습니다.",
        }[status]
        if reason:
            msg = f"{msg} ({reason})"
        self._emit("run_interrupted", status=status, reason=reason)
        return self._write_summary(status, message=msg)

    def _complete(self) -> RunSummary:
        self._set_status(RunStatus.COMPLETED, "완료")
        summary = self._write_summary(
            RunStatus.COMPLETED.value, message="완료 (test 1회 평가, final_evaluation.json 잠금)"
        )
        self._write_run_manifest()
        self._emit(
            "run_completed", selected=summary.selected, metric=summary.final_evaluation.get(self.spec.metric)
        )
        if self.lease is not None:
            self.coord.release(self.lease)
            self.lease = None
        return summary


def run_task(
    spec: TaskSpec,
    cfg: AppConfig,
    ws: Workspace,
    *,
    run_id: str | None = None,
    resume: bool = False,
    budget_override: ResourceBudget | BudgetTracker | dict[str, Any] | None = None,
    lock_hash: str | None = None,
    hooks: TrainerHooks | None = None,
    worker_id: str | None = None,
    lease_ttl_seconds: float = DEFAULT_LEASE_TTL,
) -> RunSummary:
    """TaskSpec 학습 run. 반환 status: COMPLETED / PAUSED / CANCELLED / BUDGET_EXCEEDED. FAILED/BLOCKED 는 AgentError 로 전파."""
    return TaskRunner(
        spec,
        cfg,
        ws,
        run_id=run_id,
        resume=resume,
        budget_override=budget_override,
        lock_hash=lock_hash,
        hooks=hooks,
        worker_id=worker_id,
        lease_ttl_seconds=lease_ttl_seconds,
    ).run()


def is_finite_value(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
