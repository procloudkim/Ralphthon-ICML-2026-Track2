"""Bounded, deadline-aware production runner for isolated paper reviews."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol

import anyio

from reviewharness import deadline
from reviewharness.artifacts import ArtifactPathError, ArtifactStore, JsonValue
from reviewharness.providers import ProviderError
from reviewharness.schemas import ReviewSubmission, TrustedAssignment

type _Controller = deadline.DeadlineController
type _AttemptRuntime = tuple[PaperReviewer, Path, anyio.CapacityLimiter, _Controller]


class BatchStatus(StrEnum):
    """Terminal state of one assignment in a batch."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewFailureCode(StrEnum):
    """Safe error classification without paper-derived text."""

    REVIEW_FAILED = "review_failed"
    TIMEOUT = "timeout"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    ARTIFACT_FAILED = "artifact_failed"


class PaperReviewer(Protocol):
    """High-level review kernel capability used by the runner."""

    async def review(
        self,
        assignment: TrustedAssignment,
        mode: deadline.ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter,
    ) -> ReviewSubmission:
        """Produce one validated submission within the shared model bound."""
        ...


@dataclass(frozen=True, slots=True)
class BatchConfig:
    """Production concurrency and monotonic deadline policy."""

    deadline_seconds: float = 1_500.0
    reserve_seconds: float = 120.0
    fast_mode_after_seconds: float = 600.0
    per_paper_timeout_seconds: float = 300.0
    paper_concurrency: int = 5
    model_call_concurrency: int = 10
    clock: Callable[[], float] = monotonic


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    """Terminal paper timing, routing, and safe error evidence."""

    index: int
    paper_id: str
    status: BatchStatus
    mode: deadline.ReviewMode | None = None
    used_fallback: bool = False
    elapsed_seconds: float = 0.0
    error_code: ReviewFailureCode | None = None


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """Stable input-ordered results and measured runtime metrics."""

    items: tuple[BatchItemResult, ...]
    total_seconds: float
    completion_path: Path
    completed_count: int
    failed_count: int
    fallback_count: int
    within_deadline: bool


type _AttemptSuccess = tuple[ReviewSubmission, deadline.ReviewMode, bool]
type _AttemptResult = _AttemptSuccess | ReviewFailureCode


async def run_batch(  # noqa: C901
    assignments: Sequence[TrustedAssignment],
    reviewer: PaperReviewer,
    config: BatchConfig,
    output_dir: Path,
) -> BatchSummary:
    """Review concurrently and append each completion before batch return."""
    store = ArtifactStore(output_dir)
    policy = deadline.DeadlinePolicy(
        total_seconds=config.deadline_seconds,
        reserve_seconds=config.reserve_seconds,
        fast_mode_after_seconds=config.fast_mode_after_seconds,
        per_paper_timeout_seconds=config.per_paper_timeout_seconds,
    )
    controller = deadline.DeadlineController(policy, config.clock)
    paper_limiter = anyio.CapacityLimiter(config.paper_concurrency)
    model_limiter = anyio.CapacityLimiter(config.model_call_concurrency)
    results: dict[int, BatchItemResult] = {}

    async def worker(index: int, assignment: TrustedAssignment) -> None:
        async with paper_limiter:
            try:
                decision = controller.start_paper()
                match decision:  # noqa: MATCH_OK
                    case deadline.StartPaper():
                        attempt = await _attempt(
                            assignment,
                            decision,
                            (reviewer, store.root, model_limiter, controller),
                        )
                        elapsed = max(
                            controller.metrics().elapsed_seconds
                            - decision.metrics.elapsed_seconds,
                            0.0,
                        )
                        match attempt:  # noqa: MATCH_OK
                            case (submission, mode, used_fallback):
                                _ = store.write_json(
                                    assignment.paper_id,
                                    "final_review",
                                    _submission_json(submission),
                                )
                                item = BatchItemResult(
                                    index,
                                    assignment.paper_id,
                                    BatchStatus.COMPLETED,
                                    mode=mode,
                                    used_fallback=used_fallback,
                                    elapsed_seconds=elapsed,
                                )
                            case ReviewFailureCode() as code:
                                item = BatchItemResult(
                                    index,
                                    assignment.paper_id,
                                    BatchStatus.FAILED,
                                    used_fallback=True,
                                    elapsed_seconds=elapsed,
                                    error_code=code,
                                )
                    case deadline.SkipPaper() | deadline.StopBatch():
                        item = BatchItemResult(
                            index,
                            assignment.paper_id,
                            BatchStatus.SKIPPED,
                            error_code=ReviewFailureCode.DEADLINE_EXHAUSTED,
                        )
            except (ArtifactPathError, OSError):
                item = _failure(
                    index, assignment.paper_id, ReviewFailureCode.ARTIFACT_FAILED
                )
            except (RuntimeError, ValueError):
                item = _failure(
                    index, assignment.paper_id, ReviewFailureCode.REVIEW_FAILED
                )
            results[index] = item
            receipt: dict[str, JsonValue] = {
                "elapsed_seconds": item.elapsed_seconds,
                "error_code": None
                if item.error_code is None
                else item.error_code.value,
                "mode": None if item.mode is None else item.mode.value,
                "status": item.status.value,
                "used_fallback": item.used_fallback,
            }
            try:
                _ = store.append_completion(item.paper_id, receipt)
            except (ArtifactPathError, OSError, ValueError):
                results[index] = _failure(
                    index, assignment.paper_id, ReviewFailureCode.ARTIFACT_FAILED
                )

    async def isolated_worker(index: int, assignment: TrustedAssignment) -> None:
        try:
            await worker(index, assignment)
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            # This task boundary isolates ordinary bugs; cancellation still propagates.
            item = _failure(
                index, assignment.paper_id, ReviewFailureCode.REVIEW_FAILED
            )
            results[index] = item
            receipt: dict[str, JsonValue] = {
                "elapsed_seconds": item.elapsed_seconds,
                "error_code": ReviewFailureCode.REVIEW_FAILED.value,
                "mode": None,
                "status": item.status.value,
                "used_fallback": item.used_fallback,
            }
            try:
                _ = store.append_completion(item.paper_id, receipt)
            except (ArtifactPathError, OSError, ValueError):
                results[index] = _failure(
                    index, assignment.paper_id, ReviewFailureCode.ARTIFACT_FAILED
                )

    async with anyio.create_task_group() as task_group:
        for index, assignment in enumerate(assignments):
            _ = task_group.start_soon(isolated_worker, index, assignment)
    items = tuple(results[index] for index in range(len(assignments)))
    elapsed = controller.metrics().elapsed_seconds
    return BatchSummary(
        items=items,
        total_seconds=elapsed,
        completion_path=store.completion_path,
        completed_count=sum(item.status is BatchStatus.COMPLETED for item in items),
        failed_count=sum(item.status is BatchStatus.FAILED for item in items),
        fallback_count=sum(item.used_fallback for item in items),
        within_deadline=elapsed <= config.deadline_seconds,
    )


async def _attempt(  # noqa: C901
    assignment: TrustedAssignment,
    started: deadline.StartPaper,
    runtime: _AttemptRuntime,
) -> _AttemptResult:
    reviewer, output_dir, limiter, controller = runtime
    match started.mode:  # noqa: MATCH_OK
        case deadline.ReviewMode.FULL:
            modes = (deadline.ReviewMode.FULL, deadline.ReviewMode.FAST)
        case deadline.ReviewMode.FAST:
            modes = (deadline.ReviewMode.FAST, deadline.ReviewMode.FAST)
    remaining = started.budget.allocated_seconds
    failure_code = ReviewFailureCode.REVIEW_FAILED
    for attempt_index, mode in enumerate(modes):
        if attempt_index:
            match controller.check_paper(started.budget):  # noqa: MATCH_OK
                case deadline.ContinuePaper(paper_remaining_seconds=seconds):
                    remaining = seconds
                case deadline.TimeoutPaper() | deadline.StopPaper():
                    return ReviewFailureCode.DEADLINE_EXHAUSTED
        try:
            with anyio.fail_after(remaining):
                try:
                    submission = await reviewer.review(
                        assignment, mode, output_dir, limiter
                    )
                except (OSError, ProviderError, RuntimeError, ValueError):
                    failure_code = ReviewFailureCode.REVIEW_FAILED
                else:
                    if submission.paper_id == assignment.paper_id:
                        return submission, mode, attempt_index > 0
                    failure_code = ReviewFailureCode.REVIEW_FAILED
        except TimeoutError:
            failure_code = ReviewFailureCode.TIMEOUT
    return failure_code


def _submission_json(submission: ReviewSubmission) -> JsonValue:
    return {
        "paper_id": submission.paper_id,
        "soundness": submission.soundness,
        "presentation": submission.presentation,
        "significance": submission.significance,
        "originality": submission.originality,
        "overall_recommendation": submission.overall_recommendation,
        "confidence": submission.confidence,
        "comment": submission.comment,
    }


def _failure(
    index: int,
    paper_id: str,
    code: ReviewFailureCode,
) -> BatchItemResult:
    return BatchItemResult(
        index,
        paper_id,
        BatchStatus.FAILED,
        error_code=code,
    )
