"""Verdict mapping (ADR 0005 condition 2): final_state -> (state, exit code)."""
from test_template import (
    classify_outcome, EXIT_PASSED, EXIT_FAILED, EXIT_INSTANCE_ERROR,
)


def test_passed():
    assert classify_outcome("passed") == ("passed", EXIT_PASSED)


def test_failed():
    assert classify_outcome("failed") == ("failed", EXIT_FAILED)


def test_error_is_instance_error_not_inconclusive():
    # A crash mid-test must not be soft-passed as inconclusive.
    assert classify_outcome("error") == ("instance_error", EXIT_INSTANCE_ERROR)


def test_unexpected_state_is_instance_error():
    assert classify_outcome("running") == ("instance_error", EXIT_INSTANCE_ERROR)
    assert classify_outcome(None) == ("instance_error", EXIT_INSTANCE_ERROR)


def test_exit_codes_are_distinct():
    # The codes CI keys on must not collide.
    assert len({EXIT_PASSED, EXIT_FAILED, EXIT_INSTANCE_ERROR}) == 3
