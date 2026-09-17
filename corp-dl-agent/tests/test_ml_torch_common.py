"""ml-torch 시험 공통 helper (시험 함수 없음). 작은 합성 데이터·설정·coordinator 를 만든다.

- `guarded()`: 각 시험을 벽시계 제한(기본 60초) 안에 끝내도록 감시한다 (SIGALRM 이 있는 플랫폼에서만; Windows 는 감시 없음).
  무한 대기/폴링 회귀가 생기면 멈추지 않고 TimeoutError 로 실패한다.
- `cap_torch_threads()`: 작은 MLP 학습에서 코어 수만큼 스레드를 쓰면 (다른 프로세스와 경쟁 시) 오히려 수십 배 느려지므로
  시험 프로세스의 torch 스레드 수를 제한한다. 제품 코드의 기본값은 바꾸지 않는다.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from corp_dl_agent.config import AppConfig, load_config
from corp_dl_agent.ml import compute_code_hash
from corp_dl_agent.ml.data import load_dataset
from corp_dl_agent.ml.preprocessing import FittedPreprocessor, fit_preprocessor
from corp_dl_agent.ml.split import apply_split, group_split
from corp_dl_agent.ml.synthetic import (
    CATEGORICAL_FEATURES,
    CLASSIFICATION_TARGET,
    GROUP_COLUMN,
    ID_COLUMN,
    NUMERIC_FEATURES,
    POST_COLUMN,
    REGRESSION_TARGET,
    TIME_COLUMN,
    UNITS,
    generate_clip_dataset,
)
from corp_dl_agent.ml.taskspec import TaskSpec, taskspec_from_dict
from corp_dl_agent.ml.trainer import TrainingData
from corp_dl_agent.state.budget import BudgetTracker, ResourceBudget
from corp_dl_agent.state.coordinator import Coordinator
from corp_dl_agent.state.db import StateDB
from corp_dl_agent.state.machine import RunStatus
from corp_dl_agent.workspace import Workspace

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ml"
TEST_DEADLINE_SECONDS = 60.0
TEST_TORCH_THREADS = 2


@contextmanager
def guarded(seconds: float = TEST_DEADLINE_SECONDS) -> Iterator[None]:
    """시험 하나의 벽시계 상한. 초과 시 TimeoutError (SIGALRM 이 없는 Windows 에서는 감시하지 않는다)."""
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        yield
        return

    def _timeout(signum: int, frame: Any) -> None:
        raise TimeoutError(f"시험이 {seconds:.0f}초 안에 끝나지 않았습니다 (무한 대기/폴링 의심)")

    previous = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def cap_torch_threads(n: int = TEST_TORCH_THREADS) -> int:
    """시험 프로세스의 torch intra-op 스레드 수를 제한한다. 반환: 적용된 스레드 수."""
    import torch

    threads = max(1, min(int(n), os.cpu_count() or 1))
    if torch.get_num_threads() != threads:
        torch.set_num_threads(threads)
    return threads


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_cfg(tmp_path: Path, **overrides: str) -> AppConfig:
    ov = {"paths.data_root": str(tmp_path / "작업 공간")}
    ov.update(overrides)
    return load_config(None, ov)


def small_task(
    tmp_path: Path,
    task_type: str = "regression",
    *,
    n_rows: int = 240,
    n_groups: int = 12,
    seed: int = 7,
    acceptance: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> TaskSpec:
    """tmp_path/data/<task>.csv 에 작은 합성 데이터를 쓰고 TaskSpec 을 돌려준다."""
    df = generate_clip_dataset(n_rows, n_groups, seed, task_type)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / f"{task_type}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8", lineterminator="\n")
    target = REGRESSION_TARGET if task_type == "regression" else CLASSIFICATION_TARGET
    units = {k: v for k, v in UNITS.items() if k in NUMERIC_FEATURES or k == target}
    d: dict[str, Any] = {
        "task_id": task_id or f"small_{task_type}",
        "task_type": task_type,
        "data_path": str(csv_path),
        "target": target,
        "numeric_features": list(NUMERIC_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "units": units,
        "id_column": ID_COLUMN,
        "group_column": GROUP_COLUMN,
        "split_policy": "group",
        "time_column": TIME_COLUMN,
        "seed": 42,
        "metric": "mae" if task_type == "regression" else "average_precision",
        "direction": "min" if task_type == "regression" else "max",
        "acceptance": acceptance,
        "resource_budget": {"mode": "demo"},
        "data_origin": "synthetic",
        "purpose": "시험용 합성 데이터 (실제 물리 아님)",
        "excluded_columns": [POST_COLUMN, "design_revision"],
    }
    return taskspec_from_dict(d)


def budget(epochs: int = 3, cands: int = 1, patience: int = 3, wall: int = 300) -> ResourceBudget:
    return ResourceBudget.demo(
        max_epochs=epochs, max_candidates=cands, patience=patience, wall_time_seconds=wall
    )


def tracker(
    epochs: int = 3,
    cands: int = 1,
    patience: int = 3,
    wall: int = 300,
    clock: Callable[[], float] | None = None,
) -> BudgetTracker:
    t = BudgetTracker(budget(epochs, cands, patience, wall), clock=clock, label="test")
    t.start()
    return t


def prepared_data(spec: TaskSpec, cfg: AppConfig) -> tuple[TrainingData, FittedPreprocessor, Any, Any, Any]:
    """load → split → train-only fit → TrainingData. 반환 (data, fp, train_df, val_df, test_df)."""
    import numpy as np

    df, report = load_dataset(spec, roots=[Path(spec.data_path).parent])
    manifest = group_split(df, spec, code_hash=compute_code_hash(), data_hash=report.sha256)
    train, val, test = apply_split(df, manifest)
    fp = fit_preprocessor(spec, train)
    data = TrainingData(
        task_type=spec.task_type,
        X_train=fp.transform(train),
        y_train=np.asarray(train[spec.target], dtype="float64"),
        X_val=fp.transform(val),
        y_val=np.asarray(val[spec.target], dtype="float64"),
        feature_names=list(fp.feature_names),
        metric=spec.metric,
        direction=spec.direction,
        target_unit=spec.target_unit(),
    )
    return data, fp, train, val, test


def running_coordinator(
    tmp_path: Path, run_id: str = "run-t", worker_id: str = "aaaaaaaa-1-0a0a0a0a"
) -> tuple[StateDB, Coordinator]:
    db = StateDB(tmp_path / "상태" / "agent_state.sqlite")
    coord = Coordinator(db, worker_id)
    coord.create_run(run_id, "fp", "cfg", "regression")
    coord.set_status(run_id, RunStatus.VALIDATING)
    coord.set_status(run_id, RunStatus.PLANNED)
    coord.set_status(run_id, RunStatus.RUNNING)
    return db, coord


def workspace(cfg: AppConfig) -> Workspace:
    ws = Workspace.from_config(cfg)
    ws.ensure()
    return ws


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    import json

    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
