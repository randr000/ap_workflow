"""State machine tests. End-to-end workflow tests live in test_workflow_service.py."""

import pytest

from app.domain.workflow import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    WorkflowState as S,
    assert_transition,
    can_transition,
)

HAPPY_PATH = [S.RECEIVED, S.EXTRACTED, S.CLASSIFIED, S.MATCHED, S.PENDING_APPROVAL,
              S.APPROVED, S.VALIDATED, S.POSTED]
EXCEPTION_PATH = [S.RECEIVED, S.EXTRACTED, S.CLASSIFIED, S.MATCHED, S.EXCEPTION,
                  S.PENDING_APPROVAL, S.APPROVED, S.VALIDATED, S.POSTED]


@pytest.mark.parametrize("path", [HAPPY_PATH, EXCEPTION_PATH])
def test_valid_paths(path):
    for current, nxt in zip(path, path[1:]):
        assert_transition(current, nxt)


@pytest.mark.parametrize("current,target", [
    (S.RECEIVED, S.POSTED),
    (S.PENDING_APPROVAL, S.POSTED),     # posting before approval
    (S.APPROVED, S.POSTED),             # posting before validation
    (S.EXCEPTION, S.APPROVED),          # exceptions must be routed first
    (S.POSTED, S.POSTED),               # posting twice
    (S.REJECTED, S.POSTED),             # rejected invoice cannot post
    (S.REJECTED, S.PENDING_APPROVAL),
    (S.POSTED, S.REJECTED),
])
def test_invalid_transitions(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError):
        assert_transition(current, target)


def test_terminal_states_have_no_exits():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_every_state_defined():
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_only_validated_can_post():
    assert [s for s in S if can_transition(s, S.POSTED)] == [S.VALIDATED]
