"""자원 예산(벽시계 시간/호출/토큰/후보/epoch/patience) 과 추적기.

- demo: 후보 2 / 10 epochs / patience 3 / 300 초, pilot: 6 / 100 / 10 / 3600 초 (BUILD_SPEC [9]).
- 예산은 완료 보증이 아니다. 기준 모델·평가·재시도 시간도 같은 벽시계에 포함된다.
- LLM 호출에 usage 가 없으면 reserve_tokens_per_call 만큼 보수적으로 예약한다.
- 시간 측정은 monotonic clock 기본. 시험에서는 clock 을 주입한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field

from corp_dl_agent.common import StrictModel, now_iso
from corp_dl_agent.config.schemas import AppConfig, BudgetConfig
from corp_dl_agent.errors import AgentError

BudgetMode = Literal["demo", "pilot", "custom"]


class ResourceBudget(StrictModel):
    wall_time_seconds: int = Field(ge=1)
    max_calls: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_candidates: int = Field(ge=1)
    max_epochs: int = Field(ge=1)
    patience: int = Field(ge=1)
    reserve_tokens_per_call: int = Field(default=2000, ge=0)
    mode: BudgetMode = "custom"

    @classmethod
    def demo(cls, **overrides: Any) -> ResourceBudget:
        base: dict[str, Any] = {
            "wall_time_seconds": 300,
            "max_candidates": 2,
            "max_epochs": 10,
            "patience": 3,
            "mode": "demo",
        }
        base.update(overrides)
        return cls(**base)

    @classmethod
    def pilot(cls, **overrides: Any) -> ResourceBudget:
        base: dict[str, Any] = {
            "wall_time_seconds": 3600,
            "max_candidates": 6,
            "max_epochs": 100,
            "patience": 10,
            "mode": "pilot",
        }
        base.update(overrides)
        return cls(**base)

    @classmethod
    def from_config(
        cls,
        budget: BudgetConfig,
        *,
        mode: BudgetMode = "custom",
        max_calls: int | None = None,
        max_tokens: int | None = None,
        reserve_tokens_per_call: int = 2000,
    ) -> ResourceBudget:
        """config/schemas.BudgetConfig (ml.demo / ml.pilot) 와 호환되는 변환."""
        return cls(
            wall_time_seconds=int(budget.wall_time_seconds),
            max_calls=max_calls,
            max_tokens=max_tokens,
            max_candidates=int(budget.max_candidates),
            max_epochs=int(budget.max_epochs),
            patience=int(budget.patience),
            reserve_tokens_per_call=int(reserve_tokens_per_call),
            mode=mode,
        )

    @classmethod
    def from_app_config(cls, cfg: AppConfig, mode: Literal["demo", "pilot"] = "demo") -> ResourceBudget:
        """AppConfig 의 ml.demo/ml.pilot 예산 + (gateway 사용 시) 호출/토큰 상한."""
        budget = cfg.ml.demo if mode == "demo" else cfg.ml.pilot
        use_gateway = cfg.network_allowed()
        return cls.from_config(
            budget,
            mode=mode,
            max_calls=cfg.gateway.max_calls if use_gateway else 0,
            max_tokens=cfg.gateway.max_total_tokens if use_gateway else 0,
            reserve_tokens_per_call=cfg.gateway.reserve_tokens_per_call,
        )


class BudgetTracker:
    """벽시계 시간·호출·토큰 사용량을 추적하고 초과 시 E_BUDGET_EXCEEDED 를 낸다."""

    def __init__(
        self,
        budget: ResourceBudget,
        *,
        clock: Callable[[], float] | None = None,
        label: str = "",
    ) -> None:
        self.budget = budget
        self.label = label
        self._clock: Callable[[], float] = clock or time.monotonic
        self._started_at: float | None = None
        self._started_iso: str | None = None
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_reserved = 0
        self.calls_without_usage = 0
        self.candidates_started = 0
        self.history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ time
    def start(self) -> None:
        """멱등. 두 번 호출해도 시작 시각은 바뀌지 않는다."""
        if self._started_at is None:
            self._started_at = self._clock()
            self._started_iso = now_iso()

    @property
    def started(self) -> bool:
        return self._started_at is not None

    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def remaining(self) -> float:
        return max(0.0, float(self.budget.wall_time_seconds) - self.elapsed())

    def deadline_reached(self) -> bool:
        return self.elapsed() >= float(self.budget.wall_time_seconds)

    # ------------------------------------------------------------------ tokens/calls
    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out + self.tokens_reserved

    def calls_remaining(self) -> int | None:
        if self.budget.max_calls is None:
            return None
        return max(0, self.budget.max_calls - self.calls)

    def tokens_remaining(self) -> int | None:
        if self.budget.max_tokens is None:
            return None
        return max(0, self.budget.max_tokens - self.tokens_total)

    def record_call(
        self,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        *,
        reserved: int | None = None,
        raise_on_exceed: bool = True,
    ) -> dict[str, Any]:
        """LLM/외부 호출 1회 기록. usage(tokens_in/out) 가 없으면 reserved(기본 reserve_tokens_per_call) 를 예약한다."""
        self.calls += 1
        usage_known = tokens_in is not None or tokens_out is not None
        charged_reserved = 0
        if usage_known:
            if (tokens_in is not None and tokens_in < 0) or (tokens_out is not None and tokens_out < 0):
                raise AgentError("E_INPUT_INVALID", "토큰 사용량은 음수일 수 없습니다")
            self.tokens_in += int(tokens_in or 0)
            self.tokens_out += int(tokens_out or 0)
        else:
            charged_reserved = int(reserved if reserved is not None else self.budget.reserve_tokens_per_call)
            if charged_reserved < 0:
                raise AgentError("E_INPUT_INVALID", "예약 토큰은 음수일 수 없습니다")
            self.tokens_reserved += charged_reserved
            self.calls_without_usage += 1
        entry = {
            "call": self.calls,
            "tokens_in": int(tokens_in or 0) if usage_known else None,
            "tokens_out": int(tokens_out or 0) if usage_known else None,
            "reserved": charged_reserved,
            "usage_known": usage_known,
            "elapsed": round(self.elapsed(), 3),
        }
        self.history.append(entry)
        self.check(raise_on_exceed=raise_on_exceed)
        return entry

    def record_candidate_start(self) -> int:
        """후보(trial) 시작 기록. max_candidates 초과 시 E_BUDGET_EXCEEDED."""
        if self.candidates_started >= self.budget.max_candidates:
            raise AgentError(
                "E_BUDGET_EXCEEDED",
                f"후보 수 상한({self.budget.max_candidates})에 도달했습니다",
                details=self.snapshot(),
            )
        self.candidates_started += 1
        return self.candidates_started

    # ------------------------------------------------------------------ checks
    def exceeded_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.deadline_reached():
            reasons.append(f"wall_time: {self.elapsed():.1f}s >= {self.budget.wall_time_seconds}s")
        if self.budget.max_calls is not None and self.calls > self.budget.max_calls:
            reasons.append(f"calls: {self.calls} > {self.budget.max_calls}")
        if self.budget.max_tokens is not None and self.tokens_total > self.budget.max_tokens:
            reasons.append(f"tokens: {self.tokens_total} > {self.budget.max_tokens}")
        return reasons

    def check(self, raise_on_exceed: bool = True) -> bool:
        """예산 안이면 True. 초과 시 raise_on_exceed 에 따라 E_BUDGET_EXCEEDED 또는 False."""
        reasons = self.exceeded_reasons()
        if not reasons:
            return True
        if raise_on_exceed:
            raise AgentError(
                "E_BUDGET_EXCEEDED",
                "자원 예산을 초과했습니다: " + "; ".join(reasons),
                details=self.snapshot(),
            )
        return False

    def can_start_new_work(self, estimated_seconds: float = 0.0) -> bool:
        """새 작업(후보 학습/평가/호출) 을 시작해도 되는지. 이미 초과했거나 예상 시간이 남은 시간을 넘으면 False.

        완료 보증이 아니다: 예상보다 오래 걸리면 진행 중 check() 에서 초과가 발생한다.
        """
        if estimated_seconds < 0:
            raise AgentError("E_INPUT_INVALID", "예상 소요 시간은 음수일 수 없습니다")
        if self.exceeded_reasons():
            return False
        if (
            self.budget.max_calls is not None
            and self.calls >= self.budget.max_calls
            and self.budget.max_calls > 0
        ):
            # 호출 예산이 이미 소진되면 호출이 필요한 새 작업은 시작할 수 없다 (학습은 호출 없이도 가능하므로 시간 조건만 본다)
            pass
        return self.elapsed() + float(estimated_seconds) <= float(self.budget.wall_time_seconds)

    def can_make_call(self) -> bool:
        if self.exceeded_reasons():
            return False
        if self.budget.max_calls is not None and self.calls >= self.budget.max_calls:
            return False
        if self.budget.max_tokens is not None and self.tokens_total >= self.budget.max_tokens:
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "mode": self.budget.mode,
            "started": self.started,
            "started_at": self._started_iso,
            "elapsed_seconds": round(self.elapsed(), 3),
            "remaining_seconds": round(self.remaining(), 3),
            "wall_time_seconds": self.budget.wall_time_seconds,
            "calls": self.calls,
            "max_calls": self.budget.max_calls,
            "calls_without_usage": self.calls_without_usage,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_reserved": self.tokens_reserved,
            "tokens_total": self.tokens_total,
            "max_tokens": self.budget.max_tokens,
            "candidates_started": self.candidates_started,
            "max_candidates": self.budget.max_candidates,
            "max_epochs": self.budget.max_epochs,
            "patience": self.budget.patience,
            "exceeded": self.exceeded_reasons(),
            "guarantee": "none",  # 예산은 완료시간 보증이 아님
        }
