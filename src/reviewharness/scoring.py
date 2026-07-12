"""Deterministic rubric calibration over evidence-gated review findings."""

from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat

from reviewharness.config import RubricConfig
from reviewharness.schemas import (
    CentralClaimImpact,
    DecisionRelevance,
    FindingSeverity,
    FindingStatus,
    ReviewFinding,
    ReviewScores,
    ScoreCalibration,
    ScoreProposal,
)

type UnitFloat = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]

_GUARDS: Final = (
    "soundness == 1 implies overall_recommendation <= 2",
    "verified critical finding implies overall_recommendation <= 3",
    "overall_recommendation >= 5 requires soundness >= 3",
    "overall_recommendation >= 5 requires significance >= 3",
    "overall_recommendation >= 5 requires no unresolved major finding",
    "overall_recommendation == 6 requires soundness == 4",
    "overall_recommendation == 6 requires significance == 4",
)
_VERIFIED_STATUSES: Final = frozenset(
    {FindingStatus.CONSENSUS_SUPPORTED, FindingStatus.MINORITY_SUPPORTED}
)
_RETAINED_STATUSES: Final = frozenset(
    {
        FindingStatus.CONSENSUS_SUPPORTED,
        FindingStatus.MINORITY_SUPPORTED,
        FindingStatus.CONTESTED,
        FindingStatus.SUBJECTIVE_DIVERGENCE,
        FindingStatus.PARSER_UNCERTAIN,
    }
)
_POOR: Final = 1
_FAIR: Final = 2
_GOOD: Final = 3
_EXCELLENT: Final = 4
_ACCEPT: Final = 5
_STRONG_ACCEPT: Final = 6
_PARSER_THRESHOLDS: Final = (0.3, 0.6, 0.9, 1.0)
_DISAGREEMENT_THRESHOLDS: Final = (0.25, 0.5, 0.75)
_SOUNDNESS_TERMS: Final = "soundness method evaluation evidence reproduc"
_PRESENTATION_TERMS: Final = ("presentation", "clarity", "writing", "structure")
_SIGNIFICANCE_TERMS: Final = ("significance", "impact", "utility")
_ORIGINALITY_TERMS: Final = ("originality", "novelty")
_FINDING_CAPS: Final = {
    FindingSeverity.CRITICAL: ((1, 2), (2, 3)),
    FindingSeverity.MAJOR: ((2, 2), (3, 3)),
    FindingSeverity.MINOR: ((3, 6), (3, 6)),
    FindingSeverity.OBSERVATION: ((4, 6), (4, 6)),
}


@unique
class _Dimension(StrEnum):
    SOUNDNESS = "soundness"
    PRESENTATION = "presentation"
    SIGNIFICANCE = "significance"
    ORIGINALITY = "originality"


class CalibrationContext(BaseModel):
    """Sanitized, typed inputs allowed to reach the score calibrator."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    proposal: ScoreProposal
    findings: tuple[ReviewFinding, ...]
    parser_confidence: UnitFloat = 1.0
    reviewer_disagreement: UnitFloat = 0.0
    sanitization_limited_review: StrictBool = False
    unfamiliar_paper_type: StrictBool = False


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    """All canonical score contradictions found in one score vector."""

    violations: tuple[str, ...]
    passed: bool


def _dimension(category: str) -> _Dimension | None:
    normalized = category.casefold().replace("-", "_").replace(" ", "_")
    if any(token in normalized for token in _SOUNDNESS_TERMS.split()):
        return _Dimension.SOUNDNESS
    if any(token in normalized for token in _PRESENTATION_TERMS):
        return _Dimension.PRESENTATION
    if any(token in normalized for token in _SIGNIFICANCE_TERMS):
        return _Dimension.SIGNIFICANCE
    if any(token in normalized for token in _ORIGINALITY_TERMS):
        return _Dimension.ORIGINALITY
    return None


def _finding_caps(finding: ReviewFinding) -> tuple[int, int]:
    direct_or_high = (
        finding.central_claim_impact is CentralClaimImpact.DIRECT
        or finding.decision_relevance is DecisionRelevance.HIGH
    )
    direct_caps, indirect_caps = _FINDING_CAPS[finding.severity]
    return direct_caps if direct_or_high else indirect_caps


def _is_unresolved_major(finding: ReviewFinding) -> bool:
    return (
        finding.severity is FindingSeverity.MAJOR
        and finding.status in _RETAINED_STATUSES
    )


def _is_verified_critical(finding: ReviewFinding) -> bool:
    return (
        finding.severity is FindingSeverity.CRITICAL
        and finding.status in _VERIFIED_STATUSES
    )


def validate_score_consistency(
    scores: ReviewScores,
    findings: tuple[ReviewFinding, ...],
    rubric: RubricConfig,
) -> ConsistencyReport:
    """Return every violated canonical contradiction guard without mutating scores."""
    overall = scores.overall_recommendation
    failed = (
        scores.soundness == _POOR and overall > _FAIR,
        any(_is_verified_critical(finding) for finding in findings) and overall > _GOOD,
        overall >= _ACCEPT and scores.soundness < _GOOD,
        overall >= _ACCEPT and scores.significance < _GOOD,
        overall >= _ACCEPT and any(_is_unresolved_major(item) for item in findings),
        overall == _STRONG_ACCEPT and scores.soundness != _EXCELLENT,
        overall == _STRONG_ACCEPT and scores.significance != _EXCELLENT,
    )
    configured = frozenset(rubric.consistency_guards)
    violations = tuple(
        guard
        for guard, contradiction in zip(_GUARDS, failed, strict=True)
        if contradiction or guard not in configured
    )
    return ConsistencyReport(violations=violations, passed=not violations)


def _apply_verified_findings(
    proposal: ScoreProposal,
    findings: tuple[ReviewFinding, ...],
) -> tuple[dict[_Dimension, int], int, tuple[str, ...]]:
    scores = proposal.scores
    dimensions: dict[_Dimension, int] = {
        _Dimension.SOUNDNESS: min(max(scores.soundness, _FAIR), _GOOD),
        _Dimension.PRESENTATION: min(max(scores.presentation, _FAIR), _GOOD),
        _Dimension.SIGNIFICANCE: min(max(scores.significance, _FAIR), _GOOD),
        _Dimension.ORIGINALITY: min(max(scores.originality, _FAIR), _GOOD),
    }
    overall = scores.overall_recommendation
    reasons: list[str] = []
    for finding in findings:
        dimension_cap, overall_cap = _finding_caps(finding)
        dimension = _dimension(finding.category)
        if dimension is not None:
            dimensions[dimension] = min(dimensions[dimension], dimension_cap)
        overall = min(overall, overall_cap)
        reason = f"{finding.finding_id} {finding.status.value}/{finding.severity.value}"
        reasons.append(f"{reason} cap={dimension_cap}.")
    return dimensions, overall, tuple(reasons)


def _calibrate_overall(
    proposed: int,
    dimensions: dict[_Dimension, int],
    findings: tuple[ReviewFinding, ...],
) -> int:
    overall = proposed
    soundness = dimensions[_Dimension.SOUNDNESS]
    significance = dimensions[_Dimension.SIGNIFICANCE]
    if any(_is_unresolved_major(finding) for finding in findings):
        overall = min(overall, _EXCELLENT)
    if soundness == _POOR:
        overall = min(overall, _FAIR)
    if overall >= _ACCEPT and (soundness < _GOOD or significance < _GOOD):
        overall = _EXCELLENT
    if overall == _STRONG_ACCEPT and (
        soundness != _EXCELLENT or significance != _EXCELLENT
    ):
        overall = _ACCEPT
    if overall == _POOR and not any(
        _is_verified_critical(finding) for finding in findings
    ):
        overall = _FAIR
    return overall


def _calibrate_confidence(
    context: CalibrationContext,
    findings: tuple[ReviewFinding, ...],
) -> int:
    parser_cap = bisect_right(_PARSER_THRESHOLDS, context.parser_confidence) + 1
    disagreement_cap = _ACCEPT - bisect_right(
        _DISAGREEMENT_THRESHOLDS, context.reviewer_disagreement
    )
    caps = [context.proposal.scores.confidence, parser_cap, disagreement_cap]
    if context.sanitization_limited_review or context.unfamiliar_paper_type:
        caps.append(_FAIR)
    if any(
        _is_unresolved_major(finding) and finding.status not in _VERIFIED_STATUSES
        for finding in findings
    ):
        caps.append(_GOOD)
    if any(finding.status is FindingStatus.PARSER_UNCERTAIN for finding in findings):
        caps.append(_FAIR)
    return min(caps)


def calibrate_scores(
    context: CalibrationContext,
    rubric: RubricConfig,
) -> ScoreCalibration:
    """Map one contextual proposal and verified findings to official ordinal anchors."""
    proposal = context.proposal
    retained = tuple(
        finding for finding in context.findings if finding.status in _RETAINED_STATUSES
    )
    rejected = tuple(
        finding
        for finding in context.findings
        if finding.status not in _RETAINED_STATUSES
    )
    verified = tuple(
        finding for finding in retained if finding.status in _VERIFIED_STATUSES
    )
    dimensions, proposed_overall, finding_reasons = _apply_verified_findings(
        proposal, verified
    )
    overall = _calibrate_overall(proposed_overall, dimensions, retained)
    calibrated = ReviewScores(
        soundness=dimensions[_Dimension.SOUNDNESS],
        presentation=dimensions[_Dimension.PRESENTATION],
        significance=dimensions[_Dimension.SIGNIFICANCE],
        originality=dimensions[_Dimension.ORIGINALITY],
        overall_recommendation=overall,
        confidence=_calibrate_confidence(context, retained),
    )
    report = validate_score_consistency(calibrated, retained, rubric)
    scales = rubric.scores
    overall_anchor = scales.overall_recommendation.anchors[overall]
    confidence_anchor = scales.confidence.anchors[calibrated.confidence]
    anchor_reasons = (
        f"overall_recommendation={overall}: {overall_anchor}",
        f"confidence={calibrated.confidence}: {confidence_anchor}",
    )
    reasons = (f"Used one proposal from {proposal.reviewer}; no averaging.",)
    return ScoreCalibration(
        scores=calibrated,
        retained_finding_ids=tuple(finding.finding_id for finding in retained),
        rejected_finding_ids=tuple(finding.finding_id for finding in rejected),
        rationale=" ".join(reasons + finding_reasons + anchor_reasons),
        consistency_guards_passed=report.passed,
    )
