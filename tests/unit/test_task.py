from itertools import pairwise

import pytest

from zyro.core.errors import ErrorInfo, InvalidTaskError, InvalidTaskTransition
from zyro.core.task import Task, TaskStatus, VerificationStatus


def make_task(*, max_attempts: int = 1) -> Task:
    return Task(
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
        goal="Perform bounded work",
        owner="user-1",
        max_attempts=max_attempts,
    )


def test_task_follows_canonical_success_lifecycle() -> None:
    task = make_task()
    observed = [task.status]

    task.assign_agent("agent-1")
    task.start()
    observed.append(task.status)
    task.record_execution_success({"answer": 42})
    observed.append(task.status)
    task.mark_verified("structure is consistent", "runtime_structure")
    observed.append(task.status)
    task.complete()
    observed.append(task.status)

    assert observed == [
        TaskStatus.PENDING,
        TaskStatus.RUNNING,
        TaskStatus.VERIFYING,
        TaskStatus.VERIFIED,
        TaskStatus.DONE,
    ]
    assert task.attempt_count == 1
    assert task.verification.status is VerificationStatus.VERIFIED
    assert task.completed_at is not None


def test_every_observed_success_transition_changes_state() -> None:
    states = [
        TaskStatus.PENDING,
        TaskStatus.RUNNING,
        TaskStatus.VERIFYING,
        TaskStatus.VERIFIED,
        TaskStatus.DONE,
    ]
    assert all(before is not after for before, after in pairwise(states))


def test_invalid_transition_and_duplicate_start_are_rejected() -> None:
    task = make_task(max_attempts=2)

    with pytest.raises(InvalidTaskTransition, match="invalid task transition"):
        task.complete()

    task.start()
    with pytest.raises(InvalidTaskTransition, match="invalid task transition"):
        task.start()


def test_retryable_failure_can_retry_only_within_attempt_limit() -> None:
    task = make_task(max_attempts=2)
    retryable = ErrorInfo("temporary", "Try again.", "TemporaryError", retryable=True)

    task.start()
    task.fail(retryable)
    assert task.can_retry
    task.prepare_retry()
    task.start()
    task.fail(retryable)

    assert task.status is TaskStatus.FAILED
    assert not task.can_retry
    assert task.is_final
    with pytest.raises(InvalidTaskTransition, match="attempt limit"):
        task.prepare_retry()


def test_verification_failure_can_transition_directly_to_retry() -> None:
    task = make_task(max_attempts=2)
    task.start()
    task.record_execution_success("candidate")
    failure = ErrorInfo("bad_evidence", "Evidence failed.", "Verification", retryable=True)

    task.retry_after_verification_failure(failure, "test")

    assert task.status is TaskStatus.RETRY
    assert task.verification.status is VerificationStatus.FAILED
    assert task.result is None


def test_non_final_task_can_be_cancelled_but_final_task_cannot() -> None:
    task = make_task()
    task.cancel("User cancelled.")

    assert task.status is TaskStatus.CANCELLED
    assert task.error is not None
    assert task.error.code == "task_cancelled"
    with pytest.raises(InvalidTaskTransition, match="final task"):
        task.cancel()


def test_invalid_task_data_is_rejected() -> None:
    with pytest.raises(InvalidTaskError, match="goal"):
        Task(
            task_id="task-1",
            request_id="request-1",
            correlation_id="correlation-1",
            goal=" ",
            owner="user-1",
        )
