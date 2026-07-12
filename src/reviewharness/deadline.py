"""Monotonic production-deadline decisions for isolated paper reviews."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Never, final, override


class ReviewMode(StrEnum):
    """Supported review depth selected by the deadline policy."""

    FULL = "full"
    FAST = "fast"


@final
class InvalidDeadlinePolicyError(ValueError):
    """Report a deadline-policy field whose value cannot be scheduled."""

    field: str
    reason: str

    def __init__(self, field: str, reason: str) -> None:
        """Retain the invalid field and a stable explanation."""
        self.field = field
        self.reason = reason
        super().__init__(field, reason)

    @override
    def __str__(self) -> str:
        return f"invalid {self.field}: {self.reason}"


def _invalid_policy(field: str, reason: str) -> Never:
    raise InvalidDeadlinePolicyError(field=field, reason=reason)


@dataclass(frozen=True, slots=True)
class DeadlinePolicy:
    """Validated batch and per-paper timing limits in seconds."""

    total_seconds: float
    reserve_seconds: float
    fast_mode_after_seconds: float
    per_paper_timeout_seconds: float

    def __post_init__(self) -> None:
        """Reject policies that cannot provide both work and reserve windows."""
        for field, value in (
            ("total_seconds", self.total_seconds),
            ("reserve_seconds", self.reserve_seconds),
            ("fast_mode_after_seconds", self.fast_mode_after_seconds),
            ("per_paper_timeout_seconds", self.per_paper_timeout_seconds),
        ):
            if not isfinite(value):
                _invalid_policy(field, "must be finite")

        if self.total_seconds <= 0.0:
            _invalid_policy("total_seconds", "must be positive")
        if self.reserve_seconds < 0.0:
            _invalid_policy(
                "reserve_seconds",
                "must be non-negative",
            )
        if self.reserve_seconds >= self.total_seconds:
            _invalid_policy(
                "reserve_seconds",
                "must be less than total_seconds",
            )

        usable_seconds = self.total_seconds - self.reserve_seconds
        if not 0.0 <= self.fast_mode_after_seconds < usable_seconds:
            _invalid_policy(
                "fast_mode_after_seconds",
                "must fall within the usable window",
            )
        if not 0.0 < self.per_paper_timeout_seconds <= usable_seconds:
            _invalid_policy(
                "per_paper_timeout_seconds",
                "must be positive and no longer than the usable window",
            )


@dataclass(frozen=True, slots=True)
class DeadlineMetrics:
    """Immutable elapsed and remaining batch timing evidence."""

    total_seconds: float
    reserve_seconds: float
    elapsed_seconds: float
    remaining_seconds: float
    usable_remaining_seconds: float


@dataclass(frozen=True, slots=True)
class PaperBudget:
    """One paper's monotonic start, expiry, and allocated duration."""

    started_at: float
    expires_at: float
    allocated_seconds: float


@dataclass(frozen=True, slots=True)
class StartPaper:
    """Authorize paper work with a mode and bounded budget."""

    mode: ReviewMode
    budget: PaperBudget
    metrics: DeadlineMetrics


@dataclass(frozen=True, slots=True)
class SkipPaper:
    """Reserve the remaining batch time instead of starting new work."""

    metrics: DeadlineMetrics


@dataclass(frozen=True, slots=True)
class StopBatch:
    """Represent the hard batch deadline being reached."""

    metrics: DeadlineMetrics


type StartPaperDecision = StartPaper | SkipPaper | StopBatch


@dataclass(frozen=True, slots=True)
class ContinuePaper:
    """Allow in-flight work to continue with its current recommendation."""

    mode: ReviewMode
    paper_remaining_seconds: float
    metrics: DeadlineMetrics


@dataclass(frozen=True, slots=True)
class TimeoutPaper:
    """Require bounded fallback after a paper budget expires."""

    mode: ReviewMode
    metrics: DeadlineMetrics


@dataclass(frozen=True, slots=True)
class StopPaper:
    """Require immediate cancellation at the hard batch deadline."""

    metrics: DeadlineMetrics


type PaperDeadlineDecision = ContinuePaper | TimeoutPaper | StopPaper
type MonotonicClock = Callable[[], float]


@final
class DeadlineController:
    """Make deterministic scheduling decisions from one monotonic clock."""

    __slots__ = ("_clock", "_policy", "_started_at")

    def __init__(
        self,
        policy: DeadlinePolicy,
        clock: MonotonicClock = monotonic,
    ) -> None:
        """Start a deadline window at the injected clock's current reading."""
        self._policy = policy
        self._clock = clock
        self._started_at = self._clock()

    def metrics(self) -> DeadlineMetrics:
        """Return an immutable snapshot of elapsed and remaining time."""
        return self._metrics_at(self._clock())

    def start_paper(self) -> StartPaperDecision:
        """Decide whether and how newly available paper work may start."""
        now = self._clock()
        metrics = self._metrics_at(now)
        if metrics.remaining_seconds <= 0.0:
            return StopBatch(metrics=metrics)
        if metrics.usable_remaining_seconds <= 0.0:
            return SkipPaper(metrics=metrics)

        allocated_seconds = min(
            self._policy.per_paper_timeout_seconds,
            metrics.usable_remaining_seconds,
        )
        return StartPaper(
            mode=self._mode_at(metrics.elapsed_seconds),
            budget=PaperBudget(
                started_at=now,
                expires_at=now + allocated_seconds,
                allocated_seconds=allocated_seconds,
            ),
            metrics=metrics,
        )

    def check_paper(self, budget: PaperBudget) -> PaperDeadlineDecision:
        """Decide whether in-flight paper work may continue or must fall back."""
        now = self._clock()
        metrics = self._metrics_at(now)
        if metrics.remaining_seconds <= 0.0:
            return StopPaper(metrics=metrics)

        mode = self._mode_at(metrics.elapsed_seconds)
        paper_remaining_seconds = max(budget.expires_at - now, 0.0)
        if paper_remaining_seconds <= 0.0:
            return TimeoutPaper(mode=mode, metrics=metrics)
        return ContinuePaper(
            mode=mode,
            paper_remaining_seconds=paper_remaining_seconds,
            metrics=metrics,
        )

    def _metrics_at(self, now: float) -> DeadlineMetrics:
        elapsed_seconds = now - self._started_at
        remaining_seconds = max(self._policy.total_seconds - elapsed_seconds, 0.0)
        return DeadlineMetrics(
            total_seconds=self._policy.total_seconds,
            reserve_seconds=self._policy.reserve_seconds,
            elapsed_seconds=elapsed_seconds,
            remaining_seconds=remaining_seconds,
            usable_remaining_seconds=max(
                remaining_seconds - self._policy.reserve_seconds,
                0.0,
            ),
        )

    def _mode_at(self, elapsed_seconds: float) -> ReviewMode:
        if elapsed_seconds < self._policy.fast_mode_after_seconds:
            return ReviewMode.FULL
        return ReviewMode.FAST
