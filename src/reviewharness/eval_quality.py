"""Deterministic scientific-quality proxy evaluation on controlled fixtures."""

import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, StrictInt

from reviewharness.config import load_rubric
from reviewharness.evidence import verify_and_resolve
from reviewharness.formatter import build_review_comment
from reviewharness.quality_cases import (
    QualityCase,
    build_quality_case,
    load_quality_corpus,
)
from reviewharness.schemas import (
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    ReviewFinding,
    ReviewScores,
    ScoreProposal,
    ScoreSource,
    TrustedAssignment,
    compose_review_submission,
)
from reviewharness.scoring import (
    CalibrationContext,
    calibrate_scores,
    validate_score_consistency,
)
from reviewharness.validation import ReviewValidationContext, validate_review_submission

type Rate = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
type Duration = Annotated[FiniteFloat, Field(ge=0.0)]

_ROOT: Final = Path(__file__).resolve().parents[2]
_UNAVAILABLE_REASON: Final = (
    "human labels and private judge heuristics were unavailable during development"
)


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class QualityMetrics(_StrictModel):
    """Strict finite metrics emitted by the local quality evaluator."""

    evaluated_cases: StrictInt = Field(ge=1)
    evidence_coverage: Rate
    unsupported_critique_rate: Rate
    issue_precision: Rate
    issue_recall: Rate
    minority_preservation_rate: Rate
    score_comment_consistency_rate: Rate
    valid_completion_rate: Rate
    repeatability_rate: Rate
    top_issue_stability_rate: Rate
    duration_seconds: Duration
    passed: StrictBool
    human_correlation: None = None
    human_correlation_unavailable_reason: Literal[
        "human labels and private judge heuristics were unavailable during development"
    ] = _UNAVAILABLE_REASON


@dataclass(frozen=True, slots=True)
class _CaseResult:
    retained: tuple[ReviewFinding, ...]
    rejected: tuple[ReviewFinding, ...]
    valid_completion: bool
    score_expectation_met: bool | None

    @property
    def top_issue_id(self) -> str | None:
        return self.retained[0].finding_id if self.retained else None

    def retains(self, finding_id: str) -> bool:
        return any(item.finding_id == finding_id for item in self.retained)

    def status_for(self, finding_id: str) -> FindingStatus | None:
        findings = (*self.retained, *self.rejected)
        return next(
            (item.status for item in findings if item.finding_id == finding_id),
            None,
        )


def _run_case(case: QualityCase) -> _CaseResult:
    prepared = build_quality_case(case)
    resolution = verify_and_resolve(
        prepared.candidates,
        prepared.blocks,
        (prepared.claim,),
    )
    proposed_scores = case.proposed_scores or ReviewScores(
        soundness=3,
        presentation=3,
        significance=3,
        originality=3,
        overall_recommendation=4,
        confidence=3,
    )
    proposal = ScoreProposal(
        reviewer="controlled-quality-evaluator",
        scores=proposed_scores,
        rationale="Controlled proposal for deterministic rubric evaluation.",
        finding_ids=tuple(item.finding_id for item in resolution.retained),
    )
    rubric = load_rubric()
    score_report = validate_score_consistency(
        proposed_scores, resolution.retained, rubric
    )
    calibration = calibrate_scores(
        CalibrationContext(
            proposal=proposal,
            source=ScoreSource.LOCAL_OFFLINE,
            findings=(*resolution.retained, *resolution.rejected),
        ),
        rubric,
    )
    formatted = build_review_comment(
        (prepared.claim,),
        (*resolution.retained, *resolution.rejected),
        calibration,
    )
    assignment = TrustedAssignment(
        paper_id=case.case_id,
        pdf_path=_ROOT / case.paper_path,
    )
    submission = compose_review_submission(
        assignment,
        calibration,
        formatted.comment,
    )
    validation = validate_review_submission(
        submission,
        ReviewValidationContext(
            assignment=assignment,
            claims=(prepared.claim,),
            retained_findings=resolution.retained,
            calibration=calibration,
            comment_trace=formatted.trace,
        ),
    )
    expected = case.expected_score_consistent
    score_expectation_met = (
        None if expected is None else score_report.passed is expected
    )
    return _CaseResult(
        retained=resolution.retained,
        rejected=resolution.rejected,
        valid_completion=validation.is_valid,
        score_expectation_met=score_expectation_met,
    )


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def run_quality_evaluation(output: Path) -> QualityMetrics:
    """Evaluate controlled cases and atomically write strict finite JSON metrics."""
    started = perf_counter()
    corpus = load_quality_corpus()
    first = tuple(_run_case(case) for case in corpus.cases)
    second = tuple(_run_case(case) for case in corpus.cases)
    case_results = tuple(zip(corpus.cases, first, strict=True))
    expectations = tuple(
        (expectation, result)
        for case, result in case_results
        for expectation in case.expectations
    )
    expected_retain = sum(item.expected_action == "retain" for item, _ in expectations)
    correct_retain = sum(
        item.expected_action == "retain" and result.retains(item.finding_id)
        for item, result in expectations
    )
    unsupported = tuple(
        item for item in expectations if item[0].expected_action == "reject"
    )
    unsupported_retained = sum(
        result.retains(item.finding_id) for item, result in unsupported
    )
    minorities = tuple(
        item
        for item in expectations
        if item[0].expected_status is FindingStatus.MINORITY_SUPPORTED
    )
    minority_preserved = sum(
        result.status_for(item.finding_id) is FindingStatus.MINORITY_SUPPORTED
        for item, result in minorities
    )
    factual_major = tuple(
        finding
        for result in first
        for finding in result.retained
        if finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        and finding.judgment_type in {JudgmentType.OBJECTIVE, JudgmentType.MIXED}
    )
    covered = sum(
        bool(finding.evidence) and all(item.locator for item in finding.evidence)
        for finding in factual_major
    )
    scored = tuple(item for item in first if item.score_expectation_met is not None)
    score_correct = sum(
        bool(item.score_expectation_met) and item.valid_completion for item in scored
    )
    repeatability = sum(
        left == right for left, right in zip(first, second, strict=True)
    )
    top_pairs = tuple(
        (left.top_issue_id, right.top_issue_id)
        for left, right in zip(first, second, strict=True)
        if left.top_issue_id is not None or right.top_issue_id is not None
    )
    valid_count = sum(item.valid_completion for item in first)
    metrics = QualityMetrics(
        evaluated_cases=len(corpus.cases),
        evidence_coverage=_rate(covered, len(factual_major)),
        unsupported_critique_rate=_rate(unsupported_retained, len(unsupported)),
        issue_precision=_rate(correct_retain, correct_retain + unsupported_retained),
        issue_recall=_rate(correct_retain, expected_retain),
        minority_preservation_rate=_rate(minority_preserved, len(minorities)),
        score_comment_consistency_rate=_rate(score_correct, len(scored)),
        valid_completion_rate=_rate(valid_count, len(first)),
        repeatability_rate=_rate(repeatability, len(first)),
        top_issue_stability_rate=_rate(
            sum(left == right for left, right in top_pairs), len(top_pairs)
        ),
        duration_seconds=perf_counter() - started,
        passed=(
            unsupported_retained == 0
            and correct_retain == expected_retain
            and minority_preserved == len(minorities)
            and covered == len(factual_major)
            and score_correct == len(scored)
            and valid_count == len(first)
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
        newline="\n",
    ) as stream:
        temporary = Path(stream.name)
        _ = stream.write(f"{metrics.model_dump_json(indent=2)}\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        _ = temporary.replace(output)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return metrics
