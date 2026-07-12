"""Provide typed live-run records and event transport helpers."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from time import monotonic
from typing import TYPE_CHECKING, Final, final, override

import httpx2

from .api_adapter import EventAssignment, IdempotencyKey
from .schemas import ReviewScores, ReviewSubmission

if TYPE_CHECKING:
    from pathlib import Path

    from .artifacts import JsonValue

_LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)


class LiveProvider(StrEnum):
    """Provider choices exposed at the production boundary."""

    CODEX_EXEC = "codex-exec"
    LOCAL_HEURISTIC = "local-heuristic"


class LiveReviewMode(StrEnum):
    """Actual reasoning path retained in terminal completion records."""

    CODEX_EXEC = "codex_exec"
    HEURISTIC_FALLBACK = "heuristic_fallback"


class LivePaperStatus(StrEnum):
    """Terminal live state for one server-owned assignment ordinal."""

    SUBMITTED = "submitted"
    FAILED = "failed"


@final
@dataclass(frozen=True, slots=True)
class LiveProviderUnavailableError(RuntimeError):
    """Prevent a heuristic-only configuration from reaching the event API."""

    @override
    def __str__(self) -> str:
        return "LIVE_PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class LiveRunConfig:
    """Trusted production deadline, concurrency, provider, and artifact policy."""

    provider: LiveProvider
    paper_concurrency: int
    deadline_seconds: float
    output_dir: Path


@dataclass(frozen=True, slots=True)
class LivePaperResult:
    """One terminal submission or isolated failure record."""

    paper_id: str
    ordinal: int
    review_mode: LiveReviewMode
    status: LivePaperStatus
    scores: ReviewScores | None
    receipt_verified: bool
    elapsed_seconds: float
    retries: int
    failure: str | None


@dataclass(frozen=True, slots=True)
class LiveResultContext:
    """Trusted identity, mode, timing, and retry state for one terminal result."""

    paper_id: str
    ordinal: int
    review_mode: LiveReviewMode
    started_at: float
    retries: int


@dataclass(frozen=True, slots=True)
class LiveSummary:
    """Input-ordered terminal results for all ten assignments."""

    items: tuple[LivePaperResult, ...]
    total_seconds: float

    @property
    def failed_count(self) -> int:
        """Return assignments without verified submission receipts."""
        return sum(item.status is LivePaperStatus.FAILED for item in self.items)


def create_event_client() -> httpx2.AsyncClient:
    """Create the bounded HTTP/2 event client used by the typed adapter."""
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=3,
        limits=_LIMITS,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=_TIMEOUT,
        follow_redirects=False,
    )


def idempotency_key(
    assignment: EventAssignment,
    review: ReviewSubmission,
) -> IdempotencyKey:
    """Bind a retry-safe key to the trusted ordinal and validated review."""
    digest = sha256(review.model_dump_json().encode()).hexdigest()[:16]
    return IdempotencyKey(f"reviewharness-{assignment.ordinal}-{digest}")


def success_result(
    context: LiveResultContext,
    review: ReviewSubmission,
) -> LivePaperResult:
    """Create one verified terminal result after receipt validation."""
    scores = ReviewScores(
        soundness=review.soundness,
        presentation=review.presentation,
        significance=review.significance,
        originality=review.originality,
        overall_recommendation=review.overall_recommendation,
        confidence=review.confidence,
    )
    return LivePaperResult(
        paper_id=context.paper_id,
        ordinal=context.ordinal,
        review_mode=context.review_mode,
        status=LivePaperStatus.SUBMITTED,
        scores=scores,
        receipt_verified=True,
        elapsed_seconds=monotonic() - context.started_at,
        retries=context.retries,
        failure=None,
    )


def failure_result(
    context: LiveResultContext,
    failure: str,
) -> LivePaperResult:
    """Create one isolated terminal failure without untrusted error text."""
    return LivePaperResult(
        paper_id=context.paper_id,
        ordinal=context.ordinal,
        review_mode=context.review_mode,
        status=LivePaperStatus.FAILED,
        scores=None,
        receipt_verified=False,
        elapsed_seconds=monotonic() - context.started_at,
        retries=context.retries,
        failure=failure,
    )


def completion_payload(result: LivePaperResult) -> dict[str, JsonValue]:
    """Serialize the safe terminal fields persisted for production recovery."""
    return {
        "elapsed_seconds": result.elapsed_seconds,
        "failure": result.failure,
        "receipt_verified": result.receipt_verified,
        "retries": result.retries,
        "review_mode": result.review_mode.value,
        "scores": None
        if result.scores is None
        else result.scores.model_dump(mode="json"),
        "status": result.status.value,
    }
