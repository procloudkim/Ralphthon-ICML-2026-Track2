"""Strict provider-output DTOs and paper-local canonicalization."""

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from .schemas import (
    CentralClaimImpact,
    ClaimImportance,
    ClaimLocator,
    ClaimType,
    DecisionRelevance,
    EvidenceLocator,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    NonEmptyStr,
    PaperClaim,
    PdfBlock,
    ReviewFinding,
    UnitFloat,
)

type ProviderBlockId = Annotated[
    str,
    StringConstraints(pattern=r"^p[1-9][0-9]*-b[0-9]+(?:-s[0-9]+)?$"),
]


class _ProviderModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ProviderClaimEvidence(_ProviderModel):
    """Exact visible block and quote supporting one provider claim."""

    page: StrictInt = Field(ge=1)
    block_id: ProviderBlockId
    quote: NonEmptyStr


class ProviderClaim(_ProviderModel):
    """Untrusted provider claim awaiting paper-local canonicalization."""

    claim_id: NonEmptyStr
    statement: NonEmptyStr
    importance: ClaimImportance
    claim_type: ClaimType
    reported_evidence: tuple[ProviderClaimEvidence, ...]


class ProviderFindingEvidence(_ProviderModel):
    """Exact visible block and quote supporting one provider finding."""

    page: StrictInt = Field(ge=1)
    block_id: ProviderBlockId
    quote: NonEmptyStr


class ProviderFinding(_ProviderModel):
    """Untrusted provider finding without application-owned resolution fields."""

    finding_id: NonEmptyStr
    category: NonEmptyStr
    judgment_type: JudgmentType
    severity: FindingSeverity
    statement: NonEmptyStr
    target_claim_id: NonEmptyStr | None = None
    evidence: tuple[ProviderFindingEvidence, ...]
    decision_relevance: DecisionRelevance
    recommended_check: NonEmptyStr | None = None
    confidence: UnitFloat


@unique
class ProviderRejectionKind(StrEnum):
    """Sanitized reason codes for rejected provider evidence."""

    UNKNOWN_BLOCK = "unknown_block"
    PAGE_MISMATCH = "page_mismatch"
    QUOTE_MISMATCH = "quote_mismatch"


class ProviderRejectionCount(_ProviderModel):
    """Aggregate rejection count without retaining provider or paper text."""

    reason: ProviderRejectionKind
    count: StrictInt = Field(ge=1)


class ProviderContractStats(_ProviderModel):
    """Sanitized provider-boundary acceptance counts for one paper."""

    claim_candidates: StrictInt = Field(ge=0)
    accepted_claims: StrictInt = Field(ge=0)
    evidence_candidates: StrictInt = Field(ge=0)
    accepted_evidence: StrictInt = Field(ge=0)
    finding_candidates: StrictInt = Field(ge=0)
    rejections: tuple[ProviderRejectionCount, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalProviderCandidates:
    """Canonical domain candidates plus provider-contract diagnostics."""

    claims: tuple[PaperClaim, ...]
    findings: tuple[ReviewFinding, ...]
    stats: ProviderContractStats


def canonicalize_provider_candidates(
    claims: tuple[ProviderClaim, ...],
    findings: tuple[ProviderFinding, ...],
    blocks: tuple[PdfBlock, ...],
    reviewer: str,
) -> CanonicalProviderCandidates:
    """Accept only exact visible blocks with verbatim normalized quotes."""
    by_id = {block.block_id: block for block in blocks}
    rejections: Counter[ProviderRejectionKind] = Counter()
    canonical_claims = tuple(
        claim
        for candidate in claims
        if (claim := _canonical_claim(candidate, by_id, rejections)) is not None
    )
    canonical_findings: list[ReviewFinding] = []
    evidence_candidates = 0
    accepted_evidence = 0
    for candidate in findings:
        evidence_candidates += len(candidate.evidence)
        evidence = tuple(
            locator
            for item in candidate.evidence
            if (locator := _canonical_finding_evidence(item, by_id, rejections))
            is not None
        )
        accepted_evidence += len(evidence)
        canonical_findings.append(
            ReviewFinding(
                finding_id=candidate.finding_id,
                reviewer=reviewer,
                category=candidate.category,
                judgment_type=candidate.judgment_type,
                severity=candidate.severity,
                status=FindingStatus.CANDIDATE,
                statement=candidate.statement,
                target_claim_id=candidate.target_claim_id,
                evidence=evidence,
                central_claim_impact=CentralClaimImpact.UNCERTAIN,
                decision_relevance=candidate.decision_relevance,
                recommended_check=candidate.recommended_check,
                confidence=candidate.confidence,
            )
        )
    return CanonicalProviderCandidates(
        claims=canonical_claims,
        findings=tuple(canonical_findings),
        stats=ProviderContractStats(
            claim_candidates=len(claims),
            accepted_claims=len(canonical_claims),
            evidence_candidates=evidence_candidates,
            accepted_evidence=accepted_evidence,
            finding_candidates=len(findings),
            rejections=_rejection_counts(rejections),
        ),
    )


def merge_provider_contract_stats(
    values: Sequence[ProviderContractStats],
) -> ProviderContractStats:
    """Merge per-call diagnostics without retaining provider output text."""
    rejections: Counter[ProviderRejectionKind] = Counter()
    for value in values:
        rejections.update({item.reason: item.count for item in value.rejections})
    return ProviderContractStats(
        claim_candidates=sum(item.claim_candidates for item in values),
        accepted_claims=sum(item.accepted_claims for item in values),
        evidence_candidates=sum(item.evidence_candidates for item in values),
        accepted_evidence=sum(item.accepted_evidence for item in values),
        finding_candidates=sum(item.finding_candidates for item in values),
        rejections=_rejection_counts(rejections),
    )


def _canonical_claim(
    candidate: ProviderClaim,
    blocks: dict[str, PdfBlock],
    rejections: Counter[ProviderRejectionKind],
) -> PaperClaim | None:
    locators = tuple(
        locator
        for item in candidate.reported_evidence
        if (locator := _claim_locator(item, blocks, rejections)) is not None
    )
    if not locators:
        return None
    return PaperClaim(
        claim_id=candidate.claim_id,
        statement=candidate.statement,
        importance=candidate.importance,
        claim_type=candidate.claim_type,
        reported_evidence=locators,
    )


def _claim_locator(
    evidence: ProviderClaimEvidence,
    blocks: dict[str, PdfBlock],
    rejections: Counter[ProviderRejectionKind],
) -> ClaimLocator | None:
    block, reason = _supported_block(
        evidence.page,
        evidence.block_id,
        evidence.quote,
        blocks,
    )
    if block is None:
        if reason is not None:
            rejections[reason] += 1
        return None
    return ClaimLocator(
        page=block.page,
        section=block.section,
        locator=block.locator,
        block_id=block.block_id,
    )


def _canonical_finding_evidence(
    evidence: ProviderFindingEvidence,
    blocks: dict[str, PdfBlock],
    rejections: Counter[ProviderRejectionKind],
) -> EvidenceLocator | None:
    block, reason = _supported_block(
        evidence.page,
        evidence.block_id,
        evidence.quote,
        blocks,
    )
    if block is None:
        if reason is not None:
            rejections[reason] += 1
        return None
    return EvidenceLocator(
        page=block.page,
        section=block.section,
        locator=block.locator,
        summary=evidence.quote,
        block_id=block.block_id,
    )


def _supported_block(
    page: int,
    block_id: str,
    quote: str,
    blocks: dict[str, PdfBlock],
) -> tuple[PdfBlock | None, ProviderRejectionKind | None]:
    block = blocks.get(block_id)
    if block is None:
        return None, ProviderRejectionKind.UNKNOWN_BLOCK
    if block.page != page:
        return None, ProviderRejectionKind.PAGE_MISMATCH
    normalized_quote = _normalize(quote)
    if not normalized_quote or normalized_quote not in _normalize(block.text):
        return None, ProviderRejectionKind.QUOTE_MISMATCH
    return block, None


def _rejection_counts(
    values: Counter[ProviderRejectionKind],
) -> tuple[ProviderRejectionCount, ...]:
    return tuple(
        ProviderRejectionCount(reason=reason, count=count)
        for reason, count in sorted(values.items(), key=lambda item: item[0].value)
        if count > 0
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    alphanumeric = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    return " ".join(alphanumeric.split())
