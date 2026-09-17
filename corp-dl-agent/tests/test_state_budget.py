"""자원 예산(ResourceBudget/BudgetTracker) 시험: 시간/호출/토큰 한도, 보수적 토큰 예약, 새 작업 시작 거부."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corp_dl_agent.common import validate_strict
from corp_dl_agent.config.schemas import AppConfig, BudgetConfig
from corp_dl_agent.errors import AgentError
from corp_dl_agent.state.budget import BudgetTracker, ResourceBudget


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_demo_defaults_match_spec() -> None:
    b = ResourceBudget.demo()
    assert (b.max_candidates, b.max_epochs, b.patience, b.wall_time_seconds) == (2, 10, 3, 300)
    assert b.mode == "demo"
    assert b.max_calls is None and b.max_tokens is None


def test_pilot_defaults_match_spec() -> None:
    b = ResourceBudget.pilot()
    assert (b.max_candidates, b.max_epochs, b.patience, b.wall_time_seconds) == (6, 100, 10, 3600)
    assert b.mode == "pilot"


def test_from_config_is_compatible_with_budget_config() -> None:
    ml = AppConfig().ml  # 기본 AppConfig 의 ml.demo / ml.pilot
    demo = ResourceBudget.from_config(ml.demo, mode="demo")
    pilot = ResourceBudget.from_config(ml.pilot, mode="pilot")
    assert demo == ResourceBudget.demo()
    assert pilot == ResourceBudget.pilot()
    custom = ResourceBudget.from_config(
        BudgetConfig(max_candidates=3, max_epochs=20, patience=5, wall_time_seconds=600),
        max_calls=4,
        max_tokens=8000,
        reserve_tokens_per_call=100,
    )
    assert custom.mode == "custom"
    assert (custom.max_candidates, custom.max_epochs, custom.patience, custom.wall_time_seconds) == (
        3,
        20,
        5,
        600,
    )
    assert (custom.max_calls, custom.max_tokens, custom.reserve_tokens_per_call) == (4, 8000, 100)


def test_from_app_config_offline_has_zero_call_budget() -> None:
    cfg = AppConfig()  # corp-offline 기본: gateway 미사용 → 호출 0회
    b = ResourceBudget.from_app_config(cfg, "demo")
    assert b.mode == "demo" and b.max_calls == 0 and b.max_tokens == 0
    assert b.wall_time_seconds == 300
    p = ResourceBudget.from_app_config(cfg, "pilot")
    assert p.mode == "pilot" and p.wall_time_seconds == 3600
    tracker = BudgetTracker(b)
    assert tracker.can_make_call() is False


def test_from_app_config_gateway_uses_gateway_limits() -> None:
    cfg = validate_strict(
        AppConfig,
        {
            "profile": "corp-gateway",
            "gateway": {
                "enabled": True,
                "base_url": "https://gateway.example.internal",
                "model_id": "placeholder-model",
                "secret_ref": "env:PLACEHOLDER_TOKEN",
                "approved_origins": ["https://gateway.example.internal"],
                "max_calls": 5,
                "max_total_tokens": 9000,
                "reserve_tokens_per_call": 1500,
            },
        },
    )
    b = ResourceBudget.from_app_config(cfg, "demo")
    assert (b.max_calls, b.max_tokens, b.reserve_tokens_per_call) == (5, 9000, 1500)


def test_invalid_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        ResourceBudget(wall_time_seconds=0, max_candidates=1, max_epochs=1, patience=1)
    with pytest.raises(ValidationError):
        ResourceBudget.demo(max_candidates=0)
    with pytest.raises(ValidationError):
        ResourceBudget.demo(unknown_field=1)  # extra=forbid


def test_elapsed_is_zero_before_start_and_start_is_idempotent() -> None:
    clock = FakeClock(100.0)
    t = BudgetTracker(ResourceBudget.demo(), clock=clock)
    assert t.started is False
    assert t.elapsed() == 0.0
    assert t.remaining() == 300.0
    t.start()
    clock.advance(5)
    t.start()  # 두 번째 start 는 시작 시각을 바꾸지 않는다
    assert t.elapsed() == 5.0
    assert t.remaining() == 295.0


def test_wall_time_exceeded_raises_budget_exceeded() -> None:
    clock = FakeClock()
    t = BudgetTracker(ResourceBudget.demo(), clock=clock, label="run-demo")
    t.start()
    clock.advance(299)
    assert t.check() is True
    clock.advance(2)
    assert t.deadline_reached()
    assert t.remaining() == 0.0
    assert t.check(raise_on_exceed=False) is False
    with pytest.raises(AgentError) as ei:
        t.check()
    assert ei.value.code == "E_BUDGET_EXCEEDED"
    assert ei.value.exit_code == 7
    assert ei.value.details["elapsed_seconds"] == 301.0
    assert any(r.startswith("wall_time") for r in ei.value.details["exceeded"])


def test_can_start_new_work_refuses_when_estimate_exceeds_remaining_time() -> None:
    clock = FakeClock()
    t = BudgetTracker(ResourceBudget.demo(wall_time_seconds=100), clock=clock)
    t.start()
    clock.advance(70)
    assert t.can_start_new_work(20) is True
    assert t.can_start_new_work(30) is True  # 정확히 한도까지는 허용
    assert t.can_start_new_work(31) is False
    clock.advance(30)
    assert t.can_start_new_work(0) is False  # 시간 한도 도달: 새 작업 시작 거부
    with pytest.raises(AgentError):
        t.can_start_new_work(-1)


def test_call_limit_enforced() -> None:
    t = BudgetTracker(ResourceBudget.demo(max_calls=2), clock=FakeClock())
    t.start()
    t.record_call(10, 5)
    assert t.can_make_call() is True
    t.record_call(10, 5)
    assert t.can_make_call() is False
    assert t.calls_remaining() == 0
    with pytest.raises(AgentError) as ei:
        t.record_call(1, 1)
    assert ei.value.code == "E_BUDGET_EXCEEDED"
    assert t.calls == 3  # 초과 호출도 기록은 남는다
    assert t.can_start_new_work(1) is False


def test_missing_usage_reserves_tokens_conservatively() -> None:
    t = BudgetTracker(ResourceBudget.demo(reserve_tokens_per_call=2000), clock=FakeClock())
    t.start()
    e1 = t.record_call()  # usage 없음 → 기본 예약
    assert e1["usage_known"] is False and e1["reserved"] == 2000
    assert t.tokens_reserved == 2000 and t.calls_without_usage == 1
    t.record_call(reserved=500)
    assert t.tokens_reserved == 2500
    e3 = t.record_call(10, 20)
    assert e3["usage_known"] is True and e3["reserved"] == 0
    assert (t.tokens_in, t.tokens_out, t.tokens_total) == (10, 20, 2530)
    assert len(t.history) == 3


def test_token_limit_enforced() -> None:
    t = BudgetTracker(ResourceBudget.demo(max_tokens=3000), clock=FakeClock())
    t.start()
    t.record_call()  # 2000 예약
    assert t.tokens_remaining() == 1000
    with pytest.raises(AgentError) as ei:
        t.record_call()  # 4000 > 3000
    assert ei.value.code == "E_BUDGET_EXCEEDED"
    assert any(r.startswith("tokens") for r in ei.value.details["exceeded"])


def test_negative_usage_rejected() -> None:
    t = BudgetTracker(ResourceBudget.demo(), clock=FakeClock())
    with pytest.raises(AgentError) as ei:
        t.record_call(-1, 0)
    assert ei.value.code == "E_INPUT_INVALID"
    with pytest.raises(AgentError):
        t.record_call(reserved=-5)


def test_candidate_limit() -> None:
    t = BudgetTracker(ResourceBudget.demo(), clock=FakeClock())
    assert t.record_candidate_start() == 1
    assert t.record_candidate_start() == 2
    with pytest.raises(AgentError) as ei:
        t.record_candidate_start()
    assert ei.value.code == "E_BUDGET_EXCEEDED"


def test_snapshot_contents() -> None:
    clock = FakeClock()
    t = BudgetTracker(ResourceBudget.pilot(max_calls=3), clock=clock, label="pilot-1")
    t.start()
    clock.advance(12.5)
    t.record_call(100, 50)
    snap = t.snapshot()
    assert snap["label"] == "pilot-1" and snap["mode"] == "pilot"
    assert snap["elapsed_seconds"] == 12.5 and snap["remaining_seconds"] == 3587.5
    assert snap["calls"] == 1 and snap["max_calls"] == 3
    assert snap["tokens_total"] == 150 and snap["max_tokens"] is None
    assert snap["max_epochs"] == 100 and snap["patience"] == 10 and snap["max_candidates"] == 6
    assert snap["exceeded"] == []
    assert snap["guarantee"] == "none"  # 예산은 완료 보증이 아니다
