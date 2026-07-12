from __future__ import annotations

import pytest

from reviewharness import evidence
from reviewharness.schemas import (
    CentralClaimImpact,
    ClaimImportance,
    ClaimType,
    DecisionRelevance,
    EvidenceLocator,
    FindingSeverity,
    FindingStatus,
    JudgmentType,
    PaperClaim,
    PdfBlock,
    ReviewFinding,
)


def _block() -> PdfBlock:
    return PdfBlock(
        block_id="p2-b1",
        page=2,
        text="Table 1 reports accuracy from one seed. No variance is reported.",
        section="Experiments",
        locator="Table 1",
    )


def _claim() -> PaperClaim:
    return PaperClaim(
        claim_id="C1",
        statement="The method improves accuracy.",
        importance=ClaimImportance.CENTRAL,
        claim_type=ClaimType.EMPIRICAL,
    )


def _locator() -> EvidenceLocator:
    return EvidenceLocator(
        page=2,
        section="Experiments",
        locator="Table 1",
        summary="No variance is reported.",
        block_id="p2-b1",
    )


def _candidate() -> ReviewFinding:
    return ReviewFinding(
        finding_id="F-1",
        reviewer="evidence",
        category="reproducibility",
        judgment_type=JudgmentType.OBJECTIVE,
        severity=FindingSeverity.MAJOR,
        status=FindingStatus.CANDIDATE,
        statement="The central result lacks variance reporting.",
        target_claim_id="C1",
        evidence=(_locator(),),
        central_claim_impact=CentralClaimImpact.DIRECT,
        decision_relevance=DecisionRelevance.HIGH,
        recommended_check="Report variability across seeds.",
        confidence=0.8,
    )


def test_normalize_findings_deduplicates_stably() -> None:
    # Given
    first = _candidate()
    second = first.model_copy(
        update={
            "finding_id": "F-2",
            "reviewer": "method",
            "statement": "  the central result LACKS   variance reporting. ",
        }
    )

    # When
    forward = evidence.normalize_findings((second, first))
    reverse = evidence.normalize_findings((first, second))

    # Then
    assert forward == reverse
    assert len(forward) == 1
    assert forward[0].representative.finding_id == "F-1"
    assert tuple(item.finding_id for item in forward[0].members) == ("F-1", "F-2")


def test_verify_and_resolve_preserves_verified_central_minority() -> None:
    # Given
    candidate = _candidate()

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.rejected == ()
    assert len(result.retained) == 1
    assert result.retained[0].status is FindingStatus.MINORITY_SUPPORTED
    assert result.retained[0].priority == pytest.approx(0.96)
    assert result.retained[0].evidence == (_locator(),)


@pytest.mark.parametrize(
    "invalid_locator",
    [
        EvidenceLocator(
            page=3,
            summary="No variance is reported.",
            block_id="p2-b1",
        ),
        EvidenceLocator(
            page=2,
            summary="No variance is reported.",
            block_id="missing-block",
        ),
        EvidenceLocator(
            page=2,
            summary="No variance is reported.",
            block_id="p2-b1",
            locator="Figure 9",
        ),
        EvidenceLocator(
            page=2,
            summary="Five seeds and confidence intervals are reported.",
            block_id="p2-b1",
        ),
    ],
    ids=("missing-page", "missing-block", "missing-locator", "unsupported-summary"),
)
def test_verify_and_resolve_rejects_invalid_local_evidence(
    invalid_locator: EvidenceLocator,
) -> None:
    # Given
    candidate = _candidate().model_copy(update={"evidence": (invalid_locator,)})

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.retained == ()
    assert result.rejected[0].status is FindingStatus.UNSUPPORTED_REJECTED
    assert result.rejected[0].evidence == ()


def test_verify_and_resolve_rejects_unsupported_mixed_critical_finding() -> None:
    # Given
    candidate = _candidate().model_copy(
        update={
            "judgment_type": JudgmentType.MIXED,
            "severity": FindingSeverity.CRITICAL,
            "evidence": (),
        }
    )

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.retained == ()
    assert result.rejected[0].status is FindingStatus.UNSUPPORTED_REJECTED


def test_verify_and_resolve_rejects_claim_that_contradicts_cited_text() -> None:
    # Given
    block = _block().model_copy(
        update={"text": "Table 1 reports five seeds and includes an ablation."}
    )
    locator = _locator().model_copy(
        update={"summary": "Table 1 reports five seeds and includes an ablation."}
    )
    candidate = _candidate().model_copy(
        update={
            "statement": "The paper lacks an ablation and repeated-seed evaluation.",
            "evidence": (locator,),
        }
    )

    # When
    result = evidence.verify_and_resolve((candidate,), (block,), (_claim(),))

    # Then
    assert result.retained == ()
    assert result.rejected[0].status is FindingStatus.UNSUPPORTED_REJECTED


def test_reviewer_agreement_changes_confidence_but_not_priority() -> None:
    # Given
    first = _candidate()
    second = first.model_copy(update={"finding_id": "F-2", "reviewer": "method"})

    # When
    minority = evidence.verify_and_resolve((first,), (_block(),), (_claim(),))
    consensus = evidence.verify_and_resolve(
        (first, second),
        (_block(),),
        (_claim(),),
    )

    # Then
    assert consensus.retained[0].status is FindingStatus.CONSENSUS_SUPPORTED
    assert consensus.retained[0].confidence > minority.retained[0].confidence
    assert consensus.retained[0].priority == minority.retained[0].priority


def test_verified_minority_outranks_low_impact_consensus() -> None:
    # Given
    central = _candidate()
    background_claim = _claim().model_copy(
        update={"claim_id": "C2", "importance": ClaimImportance.BACKGROUND}
    )
    minor = central.model_copy(
        update={
            "finding_id": "F-10",
            "reviewer": "method",
            "category": "presentation",
            "statement": "The background description lacks variance reporting.",
            "target_claim_id": "C2",
            "severity": FindingSeverity.MINOR,
            "central_claim_impact": CentralClaimImpact.NONE,
            "decision_relevance": DecisionRelevance.LOW,
        }
    )
    minor_two = minor.model_copy(update={"finding_id": "F-11", "reviewer": "impact"})
    minor_three = minor.model_copy(
        update={"finding_id": "F-12", "reviewer": "evidence"}
    )

    # When
    result = evidence.verify_and_resolve(
        (minor_three, central, minor_two, minor),
        (_block(),),
        (_claim(), background_claim),
    )

    # Then
    assert result.retained[0].finding_id == "F-1"
    assert result.retained[0].status is FindingStatus.MINORITY_SUPPORTED
    assert result.retained[1].status is FindingStatus.CONSENSUS_SUPPORTED


def test_explicit_contested_finding_is_retained_with_verified_evidence() -> None:
    # Given
    candidate = _candidate().model_copy(update={"status": FindingStatus.CONTESTED})

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.retained[0].status is FindingStatus.CONTESTED


def test_subjective_divergence_is_retained_without_factual_evidence() -> None:
    # Given
    candidate = _candidate().model_copy(
        update={
            "judgment_type": JudgmentType.SUBJECTIVE,
            "status": FindingStatus.SUBJECTIVE_DIVERGENCE,
            "evidence": (),
        }
    )

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.retained[0].status is FindingStatus.SUBJECTIVE_DIVERGENCE


def test_minor_parser_uncertainty_is_retained_but_not_treated_as_evidence() -> None:
    # Given
    uncertain = _locator().model_copy(update={"block_id": "unparsed-block"})
    candidate = _candidate().model_copy(
        update={
            "severity": FindingSeverity.MINOR,
            "status": FindingStatus.PARSER_UNCERTAIN,
            "evidence": (uncertain,),
        }
    )

    # When
    result = evidence.verify_and_resolve((candidate,), (_block(),), (_claim(),))

    # Then
    assert result.rejected == ()
    assert result.retained[0].status is FindingStatus.PARSER_UNCERTAIN
    assert result.retained[0].evidence == ()
