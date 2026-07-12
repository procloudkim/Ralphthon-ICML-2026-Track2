from collections.abc import Sequence
from pathlib import Path
from typing import final

import anyio

from reviewharness.deadline import ReviewMode
from reviewharness.providers import ProviderCallError
from reviewharness.runner import (
    BatchConfig,
    BatchStatus,
    BatchSummary,
    run_batch,
)
from reviewharness.schemas import ReviewSubmission, TrustedAssignment


def _assignments(count: int) -> tuple[TrustedAssignment, ...]:
    return tuple(
        TrustedAssignment(
            paper_id=f"PAPER-{index:02d}",
            pdf_path=Path(f"paper-{index:02d}.pdf"),
        )
        for index in range(count)
    )


def _submission(paper_id: str) -> ReviewSubmission:
    return ReviewSubmission(
        paper_id=paper_id,
        soundness=3,
        presentation=3,
        significance=3,
        originality=3,
        overall_recommendation=4,
        confidence=3,
        comment=(
            "The paper presents a clearly scoped contribution. The available "
            "evidence supports a cautious positive assessment, while the final "
            "review retains paper-local limitations and actionable checks."
        ),
    )


@final
class ManualClock:
    def __init__(self) -> None:
        self.current = 100.0

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def _config(
    clock: ManualClock,
    *,
    papers: int = 5,
    models: int = 3,
    timeout: float = 300.0,
) -> BatchConfig:
    return BatchConfig(
        deadline_seconds=1_500.0,
        reserve_seconds=120.0,
        fast_mode_after_seconds=600.0,
        per_paper_timeout_seconds=timeout,
        paper_concurrency=papers,
        model_call_concurrency=models,
        clock=clock,
    )


@final
class FakeReviewer:
    def __init__(
        self,
        *,
        failures: frozenset[tuple[str, str]] | None = None,
        order: tuple[str, ...] = (),
        gate_target: int = 0,
        clock: ManualClock | None = None,
    ) -> None:
        self.active_models = 0
        self.active_papers = 0
        self.calls: list[tuple[str, str]] = []
        self.max_models = 0
        self.max_papers = 0
        self.ready = anyio.Event()
        self.release = anyio.Event()
        self._clock = clock
        self._failures: frozenset[tuple[str, str]] = failures or frozenset()
        self._gate_target = gate_target
        self._order = order
        self._order_events = {paper_id: anyio.Event() for paper_id in order}
        if order:
            self._order_events[order[0]].set()

    async def review(
        self,
        assignment: TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter,
    ) -> ReviewSubmission:
        _ = output_dir
        return await self._invoke(assignment, mode.value, shared_limiter)

    async def _invoke(  # noqa: C901
        self,
        assignment: TrustedAssignment,
        label: str,
        model_limiter: anyio.CapacityLimiter,
    ) -> ReviewSubmission:
        self.calls.append((assignment.paper_id, label))
        self.active_papers += 1
        self.max_papers = max(self.max_papers, self.active_papers)
        async with model_limiter:
            self.active_models += 1
            self.max_models = max(self.max_models, self.active_models)
            if self.active_models == self._gate_target:
                self.ready.set()
            if self._gate_target:
                await self.release.wait()
            if self._order:
                await self._order_events[assignment.paper_id].wait()
                position = self._order.index(assignment.paper_id)
                if position + 1 < len(self._order):
                    self._order_events[self._order[position + 1]].set()
            self.active_models -= 1
        self.active_papers -= 1
        if self._clock is not None and label == ReviewMode.FULL.value:
            self._clock.advance(601.0)
        if (assignment.paper_id, f"{label}:runtime") in self._failures:
            raise RuntimeError
        if (assignment.paper_id, f"{label}:provider") in self._failures:
            raise ProviderCallError(detail="synthetic provider failure")
        if (assignment.paper_id, f"{label}:value") in self._failures:
            raise ValueError
        if (assignment.paper_id, f"{label}:os") in self._failures:
            raise OSError
        if (assignment.paper_id, "crash") in self._failures:
            raise RuntimeError
        if (assignment.paper_id, label) in self._failures:
            raise TimeoutError
        return _submission(assignment.paper_id)


async def _run_releasing(
    assignments: Sequence[TrustedAssignment],
    reviewer: FakeReviewer,
    config: BatchConfig,
    output_dir: Path,
) -> BatchSummary:
    async with anyio.create_task_group() as task_group:
        handle = task_group.create_task(
            run_batch(assignments, reviewer, config, output_dir),
        )
        await reviewer.ready.wait()
        reviewer.release.set()
        return await handle
    raise AssertionError


def test_batch_completes_ten_with_bounded_concurrency_and_budget(
    tmp_path: Path,
) -> None:
    # Given
    assignments = _assignments(10)
    reviewer = FakeReviewer(gate_target=3)
    output_dir = tmp_path / "run"

    # When
    summary = anyio.run(
        _run_releasing,
        assignments,
        reviewer,
        _config(ManualClock()),
        output_dir,
    )

    # Then
    assert tuple(item.paper_id for item in summary.items) == tuple(
        assignment.paper_id for assignment in assignments
    )
    assert (summary.completed_count, summary.failed_count) == (10, 0)
    assert summary.within_deadline
    assert summary.total_seconds < 1_500.0
    assert (reviewer.max_papers, reviewer.max_models) == (5, 3)
    assert len(list(output_dir.glob("PAPER-*/final_review.json"))) == 10
    assert len(summary.completion_path.read_text("utf-8").splitlines()) == 10


def test_completion_stream_is_ready_order_while_summary_is_input_order(
    tmp_path: Path,
) -> None:
    # Given
    order = ("PAPER-02", "PAPER-00", "PAPER-01")

    # When
    summary = anyio.run(
        run_batch,
        _assignments(3),
        FakeReviewer(order=order),
        _config(ManualClock(), papers=3, models=3),
        tmp_path,
    )

    # Then
    lines = summary.completion_path.read_text("utf-8").splitlines()
    streamed = tuple(
        next(paper_id for paper_id in order if f'"paper_id":"{paper_id}"' in line)
        for line in lines
    )
    assert streamed == order
    assert tuple(item.paper_id for item in summary.items) == (
        "PAPER-00",
        "PAPER-01",
        "PAPER-02",
    )


def test_full_fast_fallback_isolates_runtime_provider_value_and_os_failures(
    tmp_path: Path,
) -> None:
    # Given
    failures = frozenset(
        (paper_id, label)
        for paper_id, labels in {
            "PAPER-00": ("full", "fast"),
            "PAPER-01": ("full",),
            "PAPER-02": ("full:runtime",),
            "PAPER-03": ("full:provider", "fast:provider"),
            "PAPER-04": ("full:value", "fast:value"),
            "PAPER-05": ("full:os", "fast:os"),
        }.items()
        for label in labels
    )
    reviewer = FakeReviewer(failures=failures)

    # When
    summary = anyio.run(
        run_batch,
        _assignments(7),
        reviewer,
        _config(ManualClock(), papers=7, models=7),
        tmp_path,
    )

    # Then
    assert summary.items[2].status is BatchStatus.COMPLETED
    assert summary.items[2].used_fallback
    assert all(item.status is BatchStatus.FAILED for item in summary.items[3:6])
    assert summary.items[6].status is BatchStatus.COMPLETED
    assert (summary.completed_count, summary.failed_count) == (3, 4)
    assert summary.fallback_count == 6


def test_monotonic_deadline_switches_failed_full_attempt_to_fast(
    tmp_path: Path,
) -> None:
    # Given
    clock = ManualClock()
    reviewer = FakeReviewer(
        failures=frozenset({("PAPER-00", "full")}),
        clock=clock,
    )

    # When
    summary = anyio.run(
        run_batch,
        _assignments(1),
        reviewer,
        _config(clock, papers=1, models=1, timeout=900.0),
        tmp_path,
    )

    # Then
    assert reviewer.calls == [("PAPER-00", "full"), ("PAPER-00", "fast")]
    assert summary.items[0].mode is ReviewMode.FAST
    assert summary.items[0].elapsed_seconds == 601.0


def test_rerun_reuses_valid_review_without_duplicate_event(tmp_path: Path) -> None:
    # Given
    assignments = _assignments(1)
    config = _config(ManualClock(), papers=1, models=1)
    _ = anyio.run(run_batch, assignments, FakeReviewer(), config, tmp_path)
    failing_reviewer = FakeReviewer(
        failures=frozenset({("PAPER-00", "full")}),
    )

    # When
    summary = anyio.run(run_batch, assignments, failing_reviewer, config, tmp_path)

    # Then
    assert summary.items[0].status is BatchStatus.CACHED
    assert failing_reviewer.calls == []
    assert len(summary.completion_path.read_text("utf-8").splitlines()) == 1
