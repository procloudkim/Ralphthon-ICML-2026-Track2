"""Fail-closed validation at the final review submission sink."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, override

from pydantic import ValidationError as PydanticValidationError

from reviewharness.schemas import (
    CommentInclusionTrace,
    DecisionRelevance,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    PaperClaim,
    ReviewFinding,
    ReviewScores,
    ReviewSubmission,
    ScoreCalibration,
    TrustedAssignment,
)

_MARKER_PATTERN: Final = re.compile(
    r"\bRH_[A-Z0-9_]*(?:CANARY|MARKER)[A-Z0-9_]*\b",
    re.IGNORECASE,
)
_URL_PATTERN: Final = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_FORBIDDEN_REQUEST_PATTERN: Final = re.compile(
    r"""\b(?:reveal|return|print|show|provide|send|expose|leak|read|access|run|
    execute|invoke|call|open|fetch|visit|browse|connect|submit|upload)\b
    [^\n]{0,80}\b(?:secrets?|credentials?|passwords?|api[ _-]*keys?|tokens?|
    environment\s+variables?|env\s+vars?|shell|terminal|commands?|powershell|bash|
    tools?|network|urls?|endpoints?)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_ACTION_PATTERN: Final = re.compile(
    r"""\b(?:should|recommend|clarif(?:y|ication)|report|provide|add|compare|
    evaluate|explain|justify|quantify|include|discuss|revise|verify|
    author\s+check)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_LOCATOR_PATTERN: Final = re.compile(
    r"\b(?:page|p\.|section|table|figure|fig\.|equation|appendix)\s*[A-Z0-9]",
    re.IGNORECASE,
)
_SEVERE_COMMENT_PATTERN: Final = re.compile(
    r"""\b(?:critical\s+flaw|major\s+concern|fundamental\s+flaw|invalidates|
    unsupported\s+central\s+claim|cannot\s+establish)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_UNCONDITIONAL_ACCEPT_PATTERN: Final = re.compile(
    r"""\b(?:strong\s+accept|recommend\s+acceptance|should\s+be\s+accepted|
    fully\s+sound|no\s+material\s+concerns?)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_REJECT_MAX: Final = 2
_CRITICAL_MAX: Final = 3
_LOW_RECOMMENDATION_MAX: Final = 3
_ACCEPT_MIN: Final = 5
_STRONG_ACCEPT: Final = 6
_GOOD_MIN: Final = 3
_EXCELLENT: Final = 4


@unique
class ValidationCode(StrEnum):
    """Stable machine-readable final-validation failure codes."""

    SCHEMA_INVALID = "schema_invalid"
    PAPER_ID_MISMATCH = "paper_id_mismatch"
    MARKER_LEAK = "marker_leak"
    FORBIDDEN_REQUEST = "forbidden_request"
    UNSUPPORTED_FINDING_RETAINED = "unsupported_finding_retained"
    FACTUAL_FINDING_UNSUPPORTED = "factual_finding_unsupported"
    FINDING_TRACE_MISMATCH = "finding_trace_mismatch"
    SCORE_TRACE_MISMATCH = "score_trace_mismatch"
    CONSISTENCY_GUARD_FAILED = "consistency_guard_failed"
    SCORE_CONTRADICTION = "score_contradiction"
    COMMENT_SCORE_CONTRADICTION = "comment_score_contradiction"
    COMMENT_NOT_CONSTRUCTIVE = "comment_not_constructive"
    EMPTY_CLAIM_LEDGER = "empty_claim_ledger"
    COMMENT_TRACE_MISMATCH = "comment_trace_mismatch"
    LOW_SCORE_WITHOUT_CITED_CONCERN = "low_score_without_cited_concern"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One sanitized validation failure without raw untrusted values."""

    code: ValidationCode


@dataclass(frozen=True, slots=True)
class ReviewValidationError(Exception):
    """Raised when a caller attempts to use a rejected final review."""

    issues: tuple[ValidationIssue, ...]

    @override
    def __str__(self) -> str:
        return f"review validation failed with {len(self.issues)} issue(s)"


@dataclass(frozen=True, slots=True)
class ReviewValidationContext:
    """Trusted evidence and score trace used to validate one submission."""

    assignment: TrustedAssignment
    claims: tuple[PaperClaim, ...]
    retained_findings: tuple[ReviewFinding, ...]
    calibration: ScoreCalibration
    comment_trace: CommentInclusionTrace


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Typed validation result that exposes payload only after all gates pass."""

    submission: ReviewSubmission | None
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        """Report whether the payload passed every final gate."""
        return self.submission is not None and not self.issues

    def require_valid(self) -> ReviewSubmission:
        """Return the submission or raise a sanitized typed failure."""
        if self.submission is None or self.issues:
            raise ReviewValidationError(issues=self.issues)
        return self.submission


def validate_review_payload(
    payload: str,
    context: ReviewValidationContext,
) -> ValidationReport:
    """Parse strict JSON and run every final evidence and security gate."""
    try:
        submission = ReviewSubmission.model_validate_json(payload)
    except PydanticValidationError:
        return ValidationReport(
            submission=None,
            issues=(ValidationIssue(ValidationCode.SCHEMA_INVALID),),
        )
    return validate_review_submission(submission, context)


def validate_review_submission(
    submission: ReviewSubmission,
    context: ReviewValidationContext,
) -> ValidationReport:
    """Validate a typed review against trusted provenance and sink policy."""
    issues: list[ValidationIssue] = []
    if submission.paper_id != context.assignment.paper_id:
        issues.append(ValidationIssue(ValidationCode.PAPER_ID_MISMATCH))
    issues.extend(_security_issues(submission.comment))
    issues.extend(_finding_issues(context))
    issues.extend(_score_issues(submission, context))
    issues.extend(_trace_issues(submission, context))
    issues.extend(_comment_issues(submission, context.retained_findings))
    validated_submission = submission if not issues else None
    return ValidationReport(submission=validated_submission, issues=tuple(issues))


def _security_issues(comment: str) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if _MARKER_PATTERN.search(comment) is not None:
        issues.append(ValidationIssue(ValidationCode.MARKER_LEAK))
    if (
        _URL_PATTERN.search(comment) is not None
        or _FORBIDDEN_REQUEST_PATTERN.search(comment) is not None
    ):
        issues.append(ValidationIssue(ValidationCode.FORBIDDEN_REQUEST))
    return tuple(issues)


def _finding_issues(context: ReviewValidationContext) -> tuple[ValidationIssue, ...]:
    findings = context.retained_findings
    issues: list[ValidationIssue] = []
    final_ids = tuple(finding.finding_id for finding in findings)
    calibration = context.calibration
    unsupported_statuses = {
        FindingStatus.CANDIDATE,
        FindingStatus.UNSUPPORTED_REJECTED,
        FindingStatus.UNSUPPORTED_HYPOTHESIS,
    }
    if any(finding.status in unsupported_statuses for finding in findings):
        issues.append(ValidationIssue(ValidationCode.UNSUPPORTED_FINDING_RETAINED))
    if any(_lacks_required_support(finding) for finding in findings):
        issues.append(ValidationIssue(ValidationCode.FACTUAL_FINDING_UNSUPPORTED))
    if (
        len(set(final_ids)) != len(final_ids)
        or set(final_ids) != set(calibration.retained_finding_ids)
        or bool(set(final_ids) & set(calibration.rejected_finding_ids))
    ):
        issues.append(ValidationIssue(ValidationCode.FINDING_TRACE_MISMATCH))
    return tuple(issues)


def _lacks_required_support(finding: ReviewFinding) -> bool:
    is_factual = finding.judgment_type in {
        JudgmentType.OBJECTIVE,
        JudgmentType.MIXED,
    }
    is_major = finding.severity in {
        FindingSeverity.CRITICAL,
        FindingSeverity.MAJOR,
    }
    has_locator = any(
        bool(evidence.section or evidence.locator or evidence.block_id)
        for evidence in finding.evidence
    )
    return (
        is_factual
        and is_major
        and (not has_locator or finding.recommended_check is None)
    )


def _score_issues(
    submission: ReviewSubmission,
    context: ReviewValidationContext,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    scores = ReviewScores(
        soundness=submission.soundness,
        presentation=submission.presentation,
        significance=submission.significance,
        originality=submission.originality,
        overall_recommendation=submission.overall_recommendation,
        confidence=submission.confidence,
    )
    if scores != context.calibration.scores:
        issues.append(ValidationIssue(ValidationCode.SCORE_TRACE_MISMATCH))
    if not context.calibration.consistency_guards_passed:
        issues.append(ValidationIssue(ValidationCode.CONSISTENCY_GUARD_FAILED))
    if _scores_contradict_findings(scores, context.retained_findings):
        issues.append(ValidationIssue(ValidationCode.SCORE_CONTRADICTION))
    return tuple(issues)


def _scores_contradict_findings(
    scores: ReviewScores,
    findings: tuple[ReviewFinding, ...],
) -> bool:
    has_critical = any(
        finding.severity is FindingSeverity.CRITICAL for finding in findings
    )
    has_major = any(
        finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        for finding in findings
    )
    return (
        (scores.soundness == 1 and scores.overall_recommendation > _REJECT_MAX)
        or (has_critical and scores.overall_recommendation > _CRITICAL_MAX)
        or (
            scores.overall_recommendation >= _ACCEPT_MIN
            and (
                scores.soundness < _GOOD_MIN
                or scores.significance < _GOOD_MIN
                or has_major
            )
        )
        or (
            scores.overall_recommendation == _STRONG_ACCEPT
            and (scores.soundness != _EXCELLENT or scores.significance != _EXCELLENT)
        )
    )


def _trace_issues(
    submission: ReviewSubmission,
    context: ReviewValidationContext,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    claim_ids = {claim.claim_id for claim in context.claims}
    retained_by_id = {
        finding.finding_id: finding for finding in context.retained_findings
    }
    included_claim_ids = set(context.comment_trace.included_claim_ids)
    included_finding_ids = set(context.comment_trace.included_finding_ids)
    if not claim_ids:
        issues.append(ValidationIssue(ValidationCode.EMPTY_CLAIM_LEDGER))
    if (
        not included_claim_ids
        or not included_claim_ids <= claim_ids
        or not included_finding_ids <= retained_by_id.keys()
    ):
        issues.append(ValidationIssue(ValidationCode.COMMENT_TRACE_MISMATCH))
    required_minority = {
        finding.finding_id
        for finding in context.retained_findings
        if finding.status is FindingStatus.MINORITY_SUPPORTED
        and finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        and finding.decision_relevance is not DecisionRelevance.LOW
    }
    if not required_minority <= included_finding_ids:
        issues.append(ValidationIssue(ValidationCode.COMMENT_TRACE_MISMATCH))
    if submission.overall_recommendation <= _LOW_RECOMMENDATION_MAX:
        has_cited_concern = any(
            finding_id in included_finding_ids
            and finding.evidence
            and finding.decision_relevance is not DecisionRelevance.LOW
            for finding_id, finding in retained_by_id.items()
        )
        if not has_cited_concern or _LOCATOR_PATTERN.search(submission.comment) is None:
            issues.append(
                ValidationIssue(ValidationCode.LOW_SCORE_WITHOUT_CITED_CONCERN)
            )
    return tuple(dict.fromkeys(issues))


def _comment_issues(
    submission: ReviewSubmission,
    findings: tuple[ReviewFinding, ...],
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    comment = submission.comment
    has_major = any(
        finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        for finding in findings
    )
    score_comment_conflict = (
        submission.overall_recommendation >= _ACCEPT_MIN
        and _SEVERE_COMMENT_PATTERN.search(comment) is not None
    ) or (
        submission.overall_recommendation <= _REJECT_MAX
        and _UNCONDITIONAL_ACCEPT_PATTERN.search(comment) is not None
    )
    if score_comment_conflict:
        issues.append(ValidationIssue(ValidationCode.COMMENT_SCORE_CONTRADICTION))
    if _ACTION_PATTERN.search(comment) is None or (
        has_major and _LOCATOR_PATTERN.search(comment) is None
    ):
        issues.append(ValidationIssue(ValidationCode.COMMENT_NOT_CONSTRUCTIVE))
    return tuple(issues)
