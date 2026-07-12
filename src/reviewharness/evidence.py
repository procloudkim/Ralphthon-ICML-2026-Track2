"""Deterministic paper-local evidence resolution."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from reviewharness.schemas import (
    CentralClaimImpact,
    ClaimImportance,
    DecisionRelevance,
    EvidenceLocator,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    PaperClaim,
    PdfBlock,
    ReviewFinding,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_SEVERITY_SCORE: Final = dict(zip(FindingSeverity, (1.0, 0.8, 0.4, 0.1), strict=True))
_IMPACT_SCORE: Final = dict(zip(CentralClaimImpact, (1.0, 0.6, 0.0, 0.3), strict=True))
_RELEVANCE_SCORE: Final = dict(zip(DecisionRelevance, (1.0, 0.6, 0.2), strict=True))
_CONFIDENCE_MODIFIER: Final[dict[FindingStatus, float]] = dict(
    zip(FindingStatus, (0.8, 1.0, 0.9, 0.65, 0.0, 0.0, 0.7, 0.5), strict=True)
)
_FACTUAL: Final = frozenset({JudgmentType.OBJECTIVE, JudgmentType.MIXED})
_PARSER_SAFE: Final = frozenset({FindingSeverity.MINOR, FindingSeverity.OBSERVATION})
_NEGATIONS: Final = frozenset(["lacks", "neither", "no", "not", "without"])


@dataclass(frozen=True, slots=True)
class FindingCluster:
    """One normalized finding and its reviewer-originated duplicates."""

    representative: ReviewFinding
    members: tuple[ReviewFinding, ...]


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    """Resolved findings separated by safe downstream disposition."""

    retained: tuple[ReviewFinding, ...]
    rejected: tuple[ReviewFinding, ...]


@dataclass(frozen=True, slots=True)
class _EvidenceAssessment:
    verified: tuple[EvidenceLocator, ...]
    strength: float
    page_seen: bool


def normalize_findings(
    candidates: Sequence[ReviewFinding],
) -> tuple[FindingCluster, ...]:
    """Collapse formatting-equivalent findings independently of input order."""
    groups: dict[tuple[str, str, str, str], list[ReviewFinding]] = {}
    for finding in candidates:
        key = (
            _normalize(finding.category),
            _normalize(finding.statement),
            finding.target_claim_id or "",
            finding.judgment_type.value,
        )
        groups.setdefault(key, []).append(finding)
    clusters: list[FindingCluster] = []
    for key in sorted(groups):
        members = tuple(sorted(groups[key], key=lambda item: item.finding_id))
        clusters.append(FindingCluster(members[0], members))
    return tuple(clusters)


def verify_and_resolve(
    candidates: Sequence[ReviewFinding],
    blocks: Sequence[PdfBlock],
    claims: Sequence[PaperClaim],
) -> EvidenceResolution:
    """Verify locators, reject unsupported facts, and resolve disagreement."""
    resolved = tuple(
        _resolve(cluster, blocks, claims) for cluster in normalize_findings(candidates)
    )
    retained = tuple(
        sorted(
            (
                finding
                for finding in resolved
                if _CONFIDENCE_MODIFIER[finding.status] > 0.0
            ),
            key=lambda finding: (-(finding.priority or 0.0), finding.finding_id),
        )
    )
    rejected = tuple(
        sorted(
            (
                finding
                for finding in resolved
                if _CONFIDENCE_MODIFIER[finding.status] == 0.0
            ),
            key=lambda finding: finding.finding_id,
        )
    )
    return EvidenceResolution(retained, rejected)


def _resolve(
    cluster: FindingCluster,
    blocks: Sequence[PdfBlock],
    claims: Sequence[PaperClaim],
) -> ReviewFinding:
    assessment = _assess(cluster, blocks)
    severity = max(
        (member.severity for member in cluster.members),
        key=_SEVERITY_SCORE.__getitem__,
    )
    relevance = max(
        (member.decision_relevance for member in cluster.members),
        key=_RELEVANCE_SCORE.__getitem__,
    )
    status = _status(cluster, assessment, severity)
    impact = _claim_impact(cluster.representative.target_claim_id, claims)
    retained = _CONFIDENCE_MODIFIER[status] > 0.0
    representative = cluster.representative
    priority = _unit(
        0.35 * assessment.strength
        + 0.35 * _IMPACT_SCORE[impact]
        + 0.20 * _SEVERITY_SCORE[severity]
        + 0.10 * _RELEVANCE_SCORE[relevance]
    )
    return ReviewFinding(
        finding_id=representative.finding_id,
        reviewer=representative.reviewer,
        category=representative.category.strip(),
        judgment_type=representative.judgment_type,
        severity=severity,
        status=status,
        statement=" ".join(representative.statement.split()),
        target_claim_id=representative.target_claim_id,
        evidence=assessment.verified if retained else (),
        central_claim_impact=impact,
        decision_relevance=relevance,
        recommended_check=representative.recommended_check,
        confidence=_confidence(cluster, assessment, status),
        priority=priority if retained else 0.0,
    )


def _assess(
    cluster: FindingCluster,
    blocks: Sequence[PdfBlock],
) -> _EvidenceAssessment:
    unique = {
        locator.model_dump_json(): locator
        for member in cluster.members
        for locator in member.evidence
    }
    locators = tuple(unique[key] for key in sorted(unique))
    verified: list[EvidenceLocator] = []
    page_seen = False
    for locator in locators:
        page_blocks = tuple(block for block in blocks if block.page == locator.page)
        page_seen = page_seen or bool(page_blocks)
        if _locator_supported(locator, page_blocks, cluster.representative.statement):
            verified.append(locator)
    strength = len(verified) / len(locators) if locators else 0.0
    return _EvidenceAssessment(tuple(verified), _unit(strength), page_seen)


def _locator_supported(
    locator: EvidenceLocator,
    page_blocks: Sequence[PdfBlock],
    statement: str,
) -> bool:
    return any(
        _normalize(locator.summary) in _normalize(block.text)
        and not _contradicts(statement, block.text)
        for block in page_blocks
        if (locator.block_id is None or locator.block_id == block.block_id)
        and _matches_reference(locator.section, block.section, block.text)
        and _matches_reference(locator.locator, block.locator, block.text)
    )


def _contradicts(statement: str, text: str) -> bool:
    statement_terms = set(_normalize(statement).split())
    text_terms = set(_normalize(text).split())
    return (
        bool(statement_terms & _NEGATIONS)
        and not bool(text_terms & _NEGATIONS)
        and bool((statement_terms - _NEGATIONS) & text_terms)
    )


def _matches_reference(
    expected: str | None,
    actual: str | None,
    text: str,
) -> bool:
    if expected is None:
        return True
    normalized = _normalize(expected)
    return (actual is not None and _normalize(actual) == normalized) or (
        normalized in _normalize(text)
    )


def _status(
    cluster: FindingCluster,
    assessment: _EvidenceAssessment,
    severity: FindingSeverity,
) -> FindingStatus:
    source_statuses = {member.status for member in cluster.members}
    if cluster.representative.judgment_type in _FACTUAL and not assessment.verified:
        parser_safe = (
            assessment.page_seen
            and FindingStatus.PARSER_UNCERTAIN in source_statuses
            and severity in _PARSER_SAFE
        )
        return (
            FindingStatus.PARSER_UNCERTAIN
            if parser_safe
            else FindingStatus.UNSUPPORTED_REJECTED
        )
    for preserved in (
        FindingStatus.CONTESTED,
        FindingStatus.PARSER_UNCERTAIN,
        FindingStatus.SUBJECTIVE_DIVERGENCE,
    ):
        if preserved in source_statuses:
            return preserved
    return (
        FindingStatus.CONSENSUS_SUPPORTED
        if _reviewer_count(cluster) > 1
        else FindingStatus.MINORITY_SUPPORTED
    )


def _claim_impact(
    target_claim_id: str | None,
    claims: Sequence[PaperClaim],
) -> CentralClaimImpact:
    claim = next(
        (item for item in claims if item.claim_id == target_claim_id),
        None,
    )
    if claim is None:
        return CentralClaimImpact.UNCERTAIN
    return {
        ClaimImportance.CENTRAL: CentralClaimImpact.DIRECT,
        ClaimImportance.SUPPORTING: CentralClaimImpact.INDIRECT,
        ClaimImportance.BACKGROUND: CentralClaimImpact.NONE,
    }[claim.importance]


def _confidence(
    cluster: FindingCluster,
    assessment: _EvidenceAssessment,
    status: FindingStatus,
) -> float:
    agreement = min(_reviewer_count(cluster) / 3.0, 1.0)
    verifier = sum(member.confidence for member in cluster.members) / len(
        cluster.members
    )
    raw = 0.40 * assessment.strength + 0.35 * agreement + 0.25 * verifier
    return _unit(raw * _CONFIDENCE_MODIFIER[status])


def _reviewer_count(cluster: FindingCluster) -> int:
    reviewers = {member.reviewer for member in cluster.members if member.reviewer}
    return max(len(reviewers), 1)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    alphanumeric = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    return " ".join(alphanumeric.split())


def _unit(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 6)
