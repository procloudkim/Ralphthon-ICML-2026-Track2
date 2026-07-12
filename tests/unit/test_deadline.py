import time
from dataclasses import FrozenInstanceError
from typing import final

import pytest

from reviewharness.deadline import (
    ContinuePaper,
    DeadlineController,
    DeadlinePolicy,
    InvalidDeadlinePolicyError,
    ReviewMode,
    SkipPaper,
    StartPaper,
    StartPaperDecision,
    StopBatch,
    StopPaper,
    TimeoutPaper,
)


@final
class FakeMonotonicClock:
    current: float

    def __init__(self, initial: float = 100.0) -> None:
        self.current = initial

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def _policy() -> DeadlinePolicy:
    return DeadlinePolicy(
        total_seconds=1_500.0,
        reserve_seconds=120.0,
        fast_mode_after_seconds=600.0,
        per_paper_timeout_seconds=300.0,
    )


def _require_start(decision: StartPaperDecision) -> StartPaper:
    assert isinstance(decision, StartPaper)
    return decision


def test_starts_full_mode_with_a_capped_per_paper_budget() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)

    # When
    decision = _require_start(controller.start_paper())

    # Then
    assert decision.mode is ReviewMode.FULL
    assert decision.budget.allocated_seconds == 300.0
    assert decision.budget.started_at == 100.0
    assert decision.budget.expires_at == 400.0
    assert decision.metrics.elapsed_seconds == 0.0
    assert decision.metrics.remaining_seconds == 1_500.0
    assert decision.metrics.usable_remaining_seconds == 1_380.0


def test_switches_new_work_to_fast_mode_at_the_threshold() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    clock.advance(600.0)

    # When
    decision = _require_start(controller.start_paper())

    # Then
    assert decision.mode is ReviewMode.FAST


def test_caps_paper_budget_at_the_reserve_boundary() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    clock.advance(1_350.0)

    # When
    decision = _require_start(controller.start_paper())

    # Then
    assert decision.mode is ReviewMode.FAST
    assert decision.budget.allocated_seconds == 30.0
    assert decision.budget.expires_at == 1_480.0


def test_skips_new_work_when_only_the_reserve_remains() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    clock.advance(1_380.0)

    # When
    decision = controller.start_paper()

    # Then
    assert decision == SkipPaper(
        metrics=decision.metrics,
    )
    assert decision.metrics.remaining_seconds == 120.0
    assert decision.metrics.usable_remaining_seconds == 0.0


def test_stops_the_batch_at_the_hard_deadline() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    clock.advance(1_500.0)

    # When
    decision = controller.start_paper()

    # Then
    assert decision == StopBatch(metrics=decision.metrics)
    assert decision.metrics.remaining_seconds == 0.0


def test_reports_per_paper_remaining_budget() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    started = _require_start(controller.start_paper())
    clock.advance(75.0)

    # When
    decision = controller.check_paper(started.budget)

    # Then
    assert decision == ContinuePaper(
        mode=ReviewMode.FULL,
        paper_remaining_seconds=225.0,
        metrics=decision.metrics,
    )


def test_recommends_fast_mode_to_in_flight_work_after_threshold() -> None:
    # Given
    clock = FakeMonotonicClock()
    policy = DeadlinePolicy(
        total_seconds=1_500.0,
        reserve_seconds=120.0,
        fast_mode_after_seconds=600.0,
        per_paper_timeout_seconds=900.0,
    )
    controller = DeadlineController(policy=policy, clock=clock)
    started = _require_start(controller.start_paper())
    clock.advance(600.0)

    # When
    decision = controller.check_paper(started.budget)

    # Then
    assert decision == ContinuePaper(
        mode=ReviewMode.FAST,
        paper_remaining_seconds=300.0,
        metrics=decision.metrics,
    )


def test_times_out_a_paper_at_its_budget_boundary() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    started = _require_start(controller.start_paper())
    clock.advance(300.0)

    # When
    decision = controller.check_paper(started.budget)

    # Then
    assert decision == TimeoutPaper(
        mode=ReviewMode.FULL,
        metrics=decision.metrics,
    )


def test_hard_stop_precedes_an_individual_paper_timeout() -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    started = _require_start(controller.start_paper())
    clock.advance(1_500.0)

    # When
    decision = controller.check_paper(started.budget)

    # Then
    assert decision == StopPaper(metrics=decision.metrics)


def test_injected_monotonic_clock_makes_wall_clock_irrelevant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    clock = FakeMonotonicClock()
    controller = DeadlineController(policy=_policy(), clock=clock)
    monkeypatch.setattr(time, "time", lambda: 10_000_000_000.0)
    clock.advance(600.0)

    # When
    decision = _require_start(controller.start_paper())

    # Then
    assert decision.mode is ReviewMode.FAST
    assert decision.metrics.elapsed_seconds == 600.0


def test_decisions_are_immutable() -> None:
    # Given
    controller = DeadlineController(policy=_policy(), clock=FakeMonotonicClock())
    decision = _require_start(controller.start_paper())
    field = "mode"

    # When / Then
    with pytest.raises(FrozenInstanceError):
        setattr(decision, field, ReviewMode.FAST)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_seconds", 0.0),
        ("reserve_seconds", -1.0),
        ("reserve_seconds", 1_500.0),
        ("fast_mode_after_seconds", -1.0),
        ("fast_mode_after_seconds", 1_380.0),
        ("per_paper_timeout_seconds", 0.0),
        ("per_paper_timeout_seconds", 1_381.0),
        ("total_seconds", float("inf")),
    ],
)
def test_rejects_invalid_policy_parameters(field: str, value: float) -> None:
    # Given
    values = {
        "total_seconds": 1_500.0,
        "reserve_seconds": 120.0,
        "fast_mode_after_seconds": 600.0,
        "per_paper_timeout_seconds": 300.0,
    }
    values[field] = value

    # When / Then
    with pytest.raises(InvalidDeadlinePolicyError, match=field):
        _ = DeadlinePolicy(**values)
