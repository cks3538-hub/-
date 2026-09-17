"""run / trial / attempt 상태 머신.

- run 상태 12종과 허용 전이표(ALLOWED_TRANSITIONS). 표 밖의 전이는 E_STATE_TRANSITION.
- trial(후보 실험) 과 attempt(한 trial 안의 실행 시도) 는 별도 상태를 가진다.
- 이 모듈은 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

from enum import StrEnum

from corp_dl_agent.errors import AgentError


class RunStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    BLOCKED_CONFIG = "BLOCKED_CONFIG"
    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


_BLOCKED: frozenset[RunStatus] = frozenset({RunStatus.BLOCKED_CONFIG, RunStatus.BLOCKED_DEPENDENCY})
_STOP: frozenset[RunStatus] = frozenset({RunStatus.CANCELLED, RunStatus.FAILED})

# 현재 상태 -> 허용되는 다음 상태. 종료 상태(COMPLETED/CANCELLED/FAILED) 는 나가는 전이가 없다.
ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.VALIDATING, *_STOP, *_BLOCKED},
    RunStatus.VALIDATING: {RunStatus.PLANNED, *_STOP, *_BLOCKED},
    RunStatus.PLANNED: {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.BUDGET_EXCEEDED, *_STOP, *_BLOCKED},
    RunStatus.RUNNING: {RunStatus.EVALUATING, RunStatus.PAUSED, RunStatus.BUDGET_EXCEEDED, *_STOP, *_BLOCKED},
    RunStatus.EVALUATING: {
        RunStatus.COMPLETED,
        RunStatus.PAUSED,
        RunStatus.BUDGET_EXCEEDED,
        *_STOP,
        *_BLOCKED,
    },
    # pause 해제는 pause 직전 상태(PLANNED/RUNNING/EVALUATING) 로 돌아간다.
    RunStatus.PAUSED: {RunStatus.PLANNED, RunStatus.RUNNING, RunStatus.EVALUATING, *_STOP},
    # 설정/의존성 차단은 원인 해소 후 다시 검증부터 시작한다.
    RunStatus.BLOCKED_CONFIG: {RunStatus.VALIDATING, RunStatus.CANCELLED},
    RunStatus.BLOCKED_DEPENDENCY: {RunStatus.VALIDATING, RunStatus.CANCELLED},
    # 예산 초과는 새 예산으로 명시적으로 재개하거나 취소한다.
    RunStatus.BUDGET_EXCEEDED: {RunStatus.PLANNED, RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.FAILED: set(),
}

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
)
ACTIVE_STATUSES: frozenset[RunStatus] = frozenset(s for s in RunStatus if s not in TERMINAL_STATUSES)
# resume 로 되돌아갈 수 있는 상태 (pause 직전 상태를 previous_status 에 보관한다)
RESUMABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.PAUSED, RunStatus.BUDGET_EXCEEDED})


class TrialStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"  # worker 정리/강제 종료 후 새 attempt 로 재개 가능
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # 동일 fingerprint 완료 trial 재사용
    CANCELLED = "CANCELLED"


TRIAL_TRANSITIONS: dict[TrialStatus, set[TrialStatus]] = {
    TrialStatus.PENDING: {
        TrialStatus.RUNNING,
        TrialStatus.SKIPPED,
        TrialStatus.CANCELLED,
        TrialStatus.FAILED,
    },
    TrialStatus.RUNNING: {
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.CANCELLED,
        TrialStatus.INTERRUPTED,
    },
    TrialStatus.INTERRUPTED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.CANCELLED},
    TrialStatus.COMPLETED: set(),
    TrialStatus.FAILED: set(),
    TrialStatus.SKIPPED: set(),
    TrialStatus.CANCELLED: set(),
}
TERMINAL_TRIAL_STATUSES: frozenset[TrialStatus] = frozenset(
    {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.SKIPPED, TrialStatus.CANCELLED}
)


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"  # OOM/NaN 등 — 새 attempt 로 재시도할 수 있다
    ABORTED = "ABORTED"  # worker 정리/중단/새 attempt 에 의해 대체


ATTEMPT_TRANSITIONS: dict[AttemptStatus, set[AttemptStatus]] = {
    AttemptStatus.RUNNING: {AttemptStatus.COMPLETED, AttemptStatus.FAILED, AttemptStatus.ABORTED},
    AttemptStatus.COMPLETED: set(),
    AttemptStatus.FAILED: set(),
    AttemptStatus.ABORTED: set(),
}


def coerce_run_status(value: RunStatus | str) -> RunStatus:
    if isinstance(value, RunStatus):
        return value
    try:
        return RunStatus(str(value))
    except ValueError as exc:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"알 수 없는 run 상태입니다: {value!r}",
            details={"allowed": [s.value for s in RunStatus]},
        ) from exc


def coerce_trial_status(value: TrialStatus | str) -> TrialStatus:
    if isinstance(value, TrialStatus):
        return value
    try:
        return TrialStatus(str(value))
    except ValueError as exc:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"알 수 없는 trial 상태입니다: {value!r}",
            details={"allowed": [s.value for s in TrialStatus]},
        ) from exc


def coerce_attempt_status(value: AttemptStatus | str) -> AttemptStatus:
    if isinstance(value, AttemptStatus):
        return value
    try:
        return AttemptStatus(str(value))
    except ValueError as exc:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"알 수 없는 attempt 상태입니다: {value!r}",
            details={"allowed": [s.value for s in AttemptStatus]},
        ) from exc


def is_terminal(status: RunStatus | str) -> bool:
    return coerce_run_status(status) in TERMINAL_STATUSES


def assert_transition(current: RunStatus | str, new: RunStatus | str) -> None:
    """run 상태 전이 검사. 허용되지 않으면 E_STATE_TRANSITION."""
    cur = coerce_run_status(current)
    nxt = coerce_run_status(new)
    allowed = ALLOWED_TRANSITIONS[cur]
    if nxt not in allowed:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"run 상태 전이가 허용되지 않습니다: {cur.value} -> {nxt.value}",
            details={"from": cur.value, "to": nxt.value, "allowed": sorted(s.value for s in allowed)},
        )


def assert_trial_transition(current: TrialStatus | str, new: TrialStatus | str) -> None:
    cur = coerce_trial_status(current)
    nxt = coerce_trial_status(new)
    allowed = TRIAL_TRANSITIONS[cur]
    if nxt not in allowed:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"trial 상태 전이가 허용되지 않습니다: {cur.value} -> {nxt.value}",
            details={"from": cur.value, "to": nxt.value, "allowed": sorted(s.value for s in allowed)},
        )


def assert_attempt_transition(current: AttemptStatus | str, new: AttemptStatus | str) -> None:
    cur = coerce_attempt_status(current)
    nxt = coerce_attempt_status(new)
    allowed = ATTEMPT_TRANSITIONS[cur]
    if nxt not in allowed:
        raise AgentError(
            "E_STATE_TRANSITION",
            f"attempt 상태 전이가 허용되지 않습니다: {cur.value} -> {nxt.value}",
            details={"from": cur.value, "to": nxt.value, "allowed": sorted(s.value for s in allowed)},
        )
