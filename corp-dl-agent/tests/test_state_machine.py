"""run/trial/attempt 상태 전이표 시험 (BUILD_SPEC [9], CONTRACT §2.2)."""

from __future__ import annotations

import pytest

from corp_dl_agent.errors import AgentError
from corp_dl_agent.state.machine import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    AttemptStatus,
    RunStatus,
    TrialStatus,
    assert_attempt_transition,
    assert_transition,
    assert_trial_transition,
    coerce_run_status,
    is_terminal,
)

SPEC_STATUSES = {
    "CREATED",
    "VALIDATING",
    "PLANNED",
    "RUNNING",
    "EVALUATING",
    "COMPLETED",
    "PAUSED",
    "CANCELLED",
    "FAILED",
    "BLOCKED_CONFIG",
    "BLOCKED_DEPENDENCY",
    "BUDGET_EXCEEDED",
}


def test_spec_statuses_present_and_table_complete() -> None:
    assert {s.value for s in RunStatus} == SPEC_STATUSES
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)
    for targets in ALLOWED_TRANSITIONS.values():
        assert all(isinstance(t, RunStatus) for t in targets)


def test_terminal_statuses_have_no_outgoing_transition() -> None:
    assert TERMINAL_STATUSES == {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
    for s in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[s] == set()
        assert is_terminal(s)
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert ACTIVE_STATUSES | TERMINAL_STATUSES == set(RunStatus)


def test_happy_path_chain_is_allowed() -> None:
    chain = [
        RunStatus.CREATED,
        RunStatus.VALIDATING,
        RunStatus.PLANNED,
        RunStatus.RUNNING,
        RunStatus.EVALUATING,
        RunStatus.COMPLETED,
    ]
    for a, b in zip(chain[:-1], chain[1:], strict=True):
        assert_transition(a, b)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (RunStatus.COMPLETED, RunStatus.RUNNING),
        (RunStatus.CREATED, RunStatus.RUNNING),
        (RunStatus.CREATED, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.CREATED),
        (RunStatus.RUNNING, RunStatus.COMPLETED),
        (RunStatus.CANCELLED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.VALIDATING),
        (RunStatus.PAUSED, RunStatus.COMPLETED),
        (RunStatus.BLOCKED_DEPENDENCY, RunStatus.RUNNING),
        (RunStatus.BUDGET_EXCEEDED, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.RUNNING),
    ],
)
def test_invalid_transition_raises(current: RunStatus, new: RunStatus) -> None:
    with pytest.raises(AgentError) as ei:
        assert_transition(current, new)
    assert ei.value.code == "E_STATE_TRANSITION"
    assert ei.value.details["from"] == current.value
    assert ei.value.details["to"] == new.value
    assert new.value not in ei.value.details["allowed"]


def test_unknown_status_string_rejected() -> None:
    with pytest.raises(AgentError) as ei:
        assert_transition("RUNNING", "FLYING")
    assert ei.value.code == "E_STATE_TRANSITION"
    assert "FLYING" in ei.value.message
    assert coerce_run_status("PAUSED") is RunStatus.PAUSED


def test_pause_and_resume_paths() -> None:
    for src in (RunStatus.PLANNED, RunStatus.RUNNING, RunStatus.EVALUATING):
        assert_transition(src, RunStatus.PAUSED)
        assert_transition(RunStatus.PAUSED, src)
    assert_transition(RunStatus.PAUSED, RunStatus.CANCELLED)
    with pytest.raises(AgentError):
        assert_transition(RunStatus.CREATED, RunStatus.PAUSED)


def test_budget_and_blocked_paths() -> None:
    assert_transition(RunStatus.RUNNING, RunStatus.BUDGET_EXCEEDED)
    assert_transition(RunStatus.BUDGET_EXCEEDED, RunStatus.RUNNING)
    assert_transition(RunStatus.BUDGET_EXCEEDED, RunStatus.CANCELLED)
    assert_transition(RunStatus.CREATED, RunStatus.BLOCKED_CONFIG)
    assert_transition(RunStatus.BLOCKED_CONFIG, RunStatus.VALIDATING)
    assert_transition(RunStatus.RUNNING, RunStatus.BLOCKED_DEPENDENCY)
    assert_transition(RunStatus.BLOCKED_DEPENDENCY, RunStatus.CANCELLED)


def test_trial_transitions() -> None:
    assert_trial_transition(TrialStatus.PENDING, TrialStatus.RUNNING)
    assert_trial_transition(TrialStatus.RUNNING, TrialStatus.INTERRUPTED)
    assert_trial_transition(TrialStatus.INTERRUPTED, TrialStatus.RUNNING)
    assert_trial_transition(TrialStatus.PENDING, TrialStatus.SKIPPED)
    with pytest.raises(AgentError) as ei:
        assert_trial_transition(TrialStatus.COMPLETED, TrialStatus.RUNNING)
    assert ei.value.code == "E_STATE_TRANSITION"
    with pytest.raises(AgentError):
        assert_trial_transition("PENDING", "COMPLETED")


def test_attempt_transitions() -> None:
    assert_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.FAILED)
    assert_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.ABORTED)
    with pytest.raises(AgentError) as ei:
        assert_attempt_transition(AttemptStatus.FAILED, AttemptStatus.RUNNING)
    assert ei.value.code == "E_STATE_TRANSITION"


def test_status_is_str_compatible() -> None:
    assert isinstance(RunStatus.RUNNING, str)
    assert RunStatus.RUNNING == "RUNNING"
    assert RunStatus("BUDGET_EXCEEDED") is RunStatus.BUDGET_EXCEEDED
