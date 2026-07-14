"""Measured deterministic runtime evaluation for the production runner."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import monotonic, monotonic_ns
from typing import Annotated, ClassVar, Literal, final, override

import anyio
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .deadline import ReviewMode
from .kernel import ReviewKernel
from .runner import BatchConfig, BatchSummary, ReviewFailureCode, run_batch
from .schemas import NonEmptyStr, ReviewSubmission, TrustedAssignment

type Count = Annotated[int, Field(strict=True, ge=0)]
type FiniteSeconds = Annotated[float, Field(strict=True, ge=0.0, allow_inf_nan=False)]

_REPOSITORY_ROOT = Path(__file__).parents[2]
_CORPUS_PATH = _REPOSITORY_ROOT / "tests" / "fixtures" / "batch" / "assignments.json"
_PAPER_COUNT = 10
_FAILURE_SCENARIO_COUNT = 3


class _StrictRuntimeModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class _RuntimeAssignment(_StrictRuntimeModel):
    paper_id: NonEmptyStr
    pdf_path: Path
    requested_mode: ReviewMode


class _RuntimeCorpus(_StrictRuntimeModel):
    schema_version: Literal["1.0"]
    provenance: Literal["synthetic_controlled_fixture"]
    assignments: tuple[_RuntimeAssignment, ...]


class RuntimeMetrics(_StrictRuntimeModel):
    """Exact strict metric schema consumed by the anonymous report."""

    paper_count: Count
    distinct_pdf_count: Count
    valid_completion_count: Count
    total_seconds: FiniteSeconds
    p50_seconds: FiniteSeconds
    p95_seconds: FiniteSeconds
    timeout_count: Count
    retry_count: Count
    fast_mode_fallback_count: Count
    invalid_output_count: Count
    failure_isolation_passed: bool
    full_mode_executed: bool
    fast_mode_executed: bool
    monotonic_deadline: Literal[True] = True
    evaluation_scope: Literal["local_synthetic_hash_distinct_pdf_batch"]
    provider_scope: Literal["local_heuristic_no_network"]
    real_provider_ten_paper_runtime_status: Literal["unverified"]
    real_provider_ten_paper_runtime_seconds: None = None


class RuntimeRunConfig(_StrictRuntimeModel):
    """Measured-run configuration saved outside the report metric schema."""

    deadline_seconds: FiniteSeconds
    paper_concurrency: Count
    model_call_concurrency: Count
    clock: Literal["time.monotonic"]


class _RuntimeFailureReason(StrEnum):
    GATES = "one or more measured runtime gates failed"
    CORPUS_SIZE = "runtime corpus must contain exactly ten papers"
    CORPUS_IDENTITIES = "runtime corpus paper identifiers must be unique"
    CORPUS_PDF_HASHES = "runtime corpus must contain ten hash-distinct PDFs"


@dataclass(frozen=True, slots=True)
class RuntimeEvaluationError(RuntimeError):
    """A measured local runtime gate failed."""

    reason: _RuntimeFailureReason

    @override
    def __str__(self) -> str:
        return self.reason.value


@final
class _RequestedModeReviewer:
    __slots__ = ("_executed_modes", "_kernel", "_requested_modes")

    def __init__(self, requested_modes: Mapping[str, ReviewMode]) -> None:
        self._requested_modes = requested_modes
        self._kernel = ReviewKernel()
        self._executed_modes: set[ReviewMode] = set()

    @property
    def executed_modes(self) -> frozenset[ReviewMode]:
        return frozenset(self._executed_modes)

    async def review(
        self,
        assignment: TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter,
    ) -> ReviewSubmission:
        _ = mode
        requested_mode = self._requested_modes[assignment.paper_id]
        self._executed_modes.add(requested_mode)
        return await self._kernel.review(
            assignment,
            requested_mode,
            output_dir,
            shared_limiter,
        )


@final
class _FailureRecoveryReviewer:
    __slots__ = ("_failed_full", "_kernel", "_paper_id")

    def __init__(self, paper_id: str) -> None:
        self._paper_id = paper_id
        self._kernel = ReviewKernel()
        self._failed_full = False

    async def review(
        self,
        assignment: TrustedAssignment,
        mode: ReviewMode,
        output_dir: Path,
        shared_limiter: anyio.CapacityLimiter,
    ) -> ReviewSubmission:
        if (
            assignment.paper_id == self._paper_id
            and mode is ReviewMode.FULL
            and not self._failed_full
        ):
            self._failed_full = True
            raise RuntimeError
        return await self._kernel.review(
            assignment,
            mode,
            output_dir,
            shared_limiter,
        )


def run_runtime_evaluation(output: Path) -> RuntimeMetrics:
    """Run the measured ten-paper offline evaluation and atomically save JSON."""
    return anyio.run(_evaluate_runtime, output)


async def _evaluate_runtime(output: Path) -> RuntimeMetrics:
    corpus = _load_corpus()
    assignments = tuple(
        TrustedAssignment(
            paper_id=case.paper_id,
            pdf_path=_REPOSITORY_ROOT / case.pdf_path,
        )
        for case in corpus.assignments
    )
    artifact_name = f"{output.stem}-artifacts-{monotonic_ns()}"
    artifact_root = output.parent.resolve() / artifact_name
    config = BatchConfig(
        deadline_seconds=1_500.0,
        reserve_seconds=120.0,
        fast_mode_after_seconds=600.0,
        per_paper_timeout_seconds=300.0,
        paper_concurrency=5,
        model_call_concurrency=10,
        clock=monotonic,
    )
    _write_model(
        artifact_root / "run_config.json",
        RuntimeRunConfig(
            deadline_seconds=config.deadline_seconds,
            paper_concurrency=config.paper_concurrency,
            model_call_concurrency=config.model_call_concurrency,
            clock="time.monotonic",
        ),
    )
    reviewer = _RequestedModeReviewer(
        {case.paper_id: case.requested_mode for case in corpus.assignments}
    )
    primary_dir = artifact_root / "primary"
    primary = await run_batch(assignments, reviewer, config, primary_dir)
    failure_dir = artifact_root / "failure-isolation"
    failure = await run_batch(
        assignments[:_FAILURE_SCENARIO_COUNT],
        _FailureRecoveryReviewer(assignments[0].paper_id),
        config,
        failure_dir,
    )
    failure_recovered = _failure_recovery_passed(failure, failure_dir)
    metrics = _build_metrics(
        primary,
        reviewer.executed_modes,
        distinct_pdf_count=_distinct_pdf_count(assignments),
        failure_recovered=failure_recovered,
    )
    _write_model(output, metrics)
    primary_passed = (
        primary.completed_count == _PAPER_COUNT
        and primary.failed_count == 0
        and primary.within_deadline
        and _primary_artifacts_pass(assignments, primary, primary_dir)
        and reviewer.executed_modes == frozenset({ReviewMode.FULL, ReviewMode.FAST})
    )
    if not primary_passed or not failure_recovered:
        raise RuntimeEvaluationError(_RuntimeFailureReason.GATES)
    return metrics


def _load_corpus() -> _RuntimeCorpus:
    corpus = _RuntimeCorpus.model_validate_json(_CORPUS_PATH.read_text("utf-8"))
    paper_ids = tuple(case.paper_id for case in corpus.assignments)
    if len(paper_ids) != _PAPER_COUNT:
        raise RuntimeEvaluationError(_RuntimeFailureReason.CORPUS_SIZE)
    if len(set(paper_ids)) != len(paper_ids):
        raise RuntimeEvaluationError(_RuntimeFailureReason.CORPUS_IDENTITIES)
    assignments = tuple(
        TrustedAssignment(
            paper_id=case.paper_id,
            pdf_path=_REPOSITORY_ROOT / case.pdf_path,
        )
        for case in corpus.assignments
    )
    if _distinct_pdf_count(assignments) != _PAPER_COUNT:
        raise RuntimeEvaluationError(_RuntimeFailureReason.CORPUS_PDF_HASHES)
    return corpus


def _build_metrics(
    summary: BatchSummary,
    executed_modes: frozenset[ReviewMode],
    *,
    distinct_pdf_count: int,
    failure_recovered: bool,
) -> RuntimeMetrics:
    durations = sorted(item.elapsed_seconds for item in summary.items)
    p95_index = max((95 * len(durations) + 99) // 100 - 1, 0)
    return RuntimeMetrics(
        paper_count=len(summary.items),
        distinct_pdf_count=distinct_pdf_count,
        valid_completion_count=summary.completed_count,
        total_seconds=summary.total_seconds,
        p50_seconds=float(median(durations)),
        p95_seconds=durations[p95_index],
        timeout_count=sum(
            item.error_code is ReviewFailureCode.TIMEOUT for item in summary.items
        ),
        retry_count=sum(item.used_fallback for item in summary.items),
        fast_mode_fallback_count=summary.fallback_count,
        invalid_output_count=len(summary.items) - summary.completed_count,
        failure_isolation_passed=failure_recovered,
        full_mode_executed=ReviewMode.FULL in executed_modes,
        fast_mode_executed=ReviewMode.FAST in executed_modes,
        evaluation_scope="local_synthetic_hash_distinct_pdf_batch",
        provider_scope="local_heuristic_no_network",
        real_provider_ten_paper_runtime_status="unverified",
    )


def _distinct_pdf_count(assignments: Sequence[TrustedAssignment]) -> int:
    return len(
        {
            sha256(assignment.pdf_path.read_bytes()).hexdigest()
            for assignment in assignments
        }
    )


def _primary_artifacts_pass(
    assignments: Sequence[TrustedAssignment],
    primary: BatchSummary,
    primary_dir: Path,
) -> bool:
    primary_valid = all(
        _valid_review(primary_dir / item.paper_id / "final_review.json", item.paper_id)
        for item in assignments
    )
    lines = primary.completion_path.read_text("utf-8").splitlines()
    return primary_valid and len(lines) == _PAPER_COUNT


def _failure_recovery_passed(
    failure: BatchSummary,
    failure_dir: Path,
) -> bool:
    first = failure.items[0]
    return (
        failure.completed_count == _FAILURE_SCENARIO_COUNT
        and failure.failed_count == 0
        and first.used_fallback
        and first.mode is ReviewMode.FAST
        and len(failure.completion_path.read_text("utf-8").splitlines())
        == _FAILURE_SCENARIO_COUNT
        and _valid_review(
            failure_dir / first.paper_id / "final_review.json",
            first.paper_id,
        )
    )


def _valid_review(path: Path, paper_id: str) -> bool:
    try:
        review = ReviewSubmission.model_validate_json(path.read_text("utf-8"))
    except (OSError, UnicodeError, ValidationError):
        return False
    return review.paper_id == paper_id


def _write_model(output: Path, model: BaseModel) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{monotonic_ns()}.tmp")
    payload = model.model_dump_json(indent=2) + "\n"
    _ = temporary.write_text(payload, encoding="utf-8")
    _ = temporary.replace(output)
