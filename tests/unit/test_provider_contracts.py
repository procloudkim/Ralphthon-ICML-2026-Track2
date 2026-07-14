"""Provider-output canonicalization rejects invented scientific authority."""

import pytest
from pydantic import ValidationError

from reviewharness.provider_contracts import (
    ProviderClaim,
    ProviderClaimEvidence,
    ProviderFinding,
    ProviderFindingEvidence,
    ProviderRejectionCount,
    ProviderRejectionKind,
    canonicalize_provider_candidates,
)
from reviewharness.schemas import (
    ClaimImportance,
    ClaimType,
    DecisionRelevance,
    FindingSeverity,
    JudgmentType,
    PdfBlock,
)


def _block() -> PdfBlock:
    return PdfBlock(
        block_id="p1-b2",
        page=1,
        text="The method improves accuracy but uses one fixed seed.",
        section="Experiments",
        locator="p1-b2",
    )


def _claim(evidence: ProviderClaimEvidence) -> ProviderClaim:
    return ProviderClaim(
        claim_id="C1",
        statement="The method improves accuracy.",
        importance=ClaimImportance.CENTRAL,
        claim_type=ClaimType.EMPIRICAL,
        reported_evidence=(evidence,),
    )


def _finding(evidence: ProviderFindingEvidence) -> ProviderFinding:
    return ProviderFinding(
        finding_id="F1",
        category="statistical_reporting",
        judgment_type=JudgmentType.MIXED,
        severity=FindingSeverity.MAJOR,
        statement="The comparison does not establish stability across runs.",
        target_claim_id="C1",
        evidence=(evidence,),
        decision_relevance=DecisionRelevance.HIGH,
        recommended_check="Report variability across repeated seeds.",
        confidence=0.9,
    )


def test_exact_visible_block_and_quote_become_canonical_candidates() -> None:
    claim_evidence = ProviderClaimEvidence(
        page=1,
        block_id="p1-b2",
        quote="improves accuracy",
    )
    finding_evidence = ProviderFindingEvidence(
        page=1,
        block_id="p1-b2",
        quote="uses one fixed seed",
    )

    result = canonicalize_provider_candidates(
        (_claim(claim_evidence),),
        (_finding(finding_evidence),),
        (_block(),),
        "tri_lens",
    )

    assert result.stats.accepted_claims == 1
    assert result.stats.accepted_evidence == 1
    assert result.claims[0].reported_evidence[0].block_id == "p1-b2"
    assert result.findings[0].evidence[0].summary == "uses one fixed seed"


@pytest.mark.parametrize(
    ("block_id", "quote"),
    [
        ("p1-b9", "uses one fixed seed"),
        ("p1-b2", "reports five seeds and confidence intervals"),
    ],
)
def test_invented_block_or_quote_cannot_create_evidence(
    block_id: str,
    quote: str,
) -> None:
    claim = _claim(ProviderClaimEvidence(page=1, block_id=block_id, quote=quote))
    finding = _finding(ProviderFindingEvidence(page=1, block_id=block_id, quote=quote))

    result = canonicalize_provider_candidates(
        (claim,),
        (finding,),
        (_block(),),
        "tri_lens",
    )

    assert result.claims == ()
    assert result.findings[0].evidence == ()
    assert result.stats.accepted_claims == 0
    assert result.stats.accepted_evidence == 0
    expected_reason = (
        ProviderRejectionKind.UNKNOWN_BLOCK
        if block_id == "p1-b9"
        else ProviderRejectionKind.QUOTE_MISMATCH
    )
    assert result.stats.rejections == (
        ProviderRejectionCount(reason=expected_reason, count=2),
    )


def test_range_shaped_legacy_locator_is_not_a_provider_block_id() -> None:
    with pytest.raises(ValidationError):
        _ = ProviderFindingEvidence(
            page=1,
            block_id="p1-b2-l1-l4",
            quote="uses one fixed seed",
        )
