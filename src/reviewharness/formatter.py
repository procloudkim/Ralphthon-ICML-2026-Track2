"""Deterministic, injection-safe construction of final review comments."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from unicodedata import category

from reviewharness.schemas import (
    ClaimImportance,
    CommentInclusionTrace,
    FindingStatus,
    PaperClaim,
    ReviewFinding,
    ScoreCalibration,
)

type SelectedFinding = tuple[ReviewFinding, str]


@dataclass(frozen=True, slots=True)
class FormattedReview:
    """Final safe comment plus the application-owned identifiers it includes."""

    comment: str
    trace: CommentInclusionTrace


_SUSPICIOUS: Final = (
    *("rh_canary", "rh_hidden", "rh_metadata"),
    *("ignore previous", "ignore prior", "system message"),
    *("reviewer instruction", "conference chair", "strong accept"),
    *("output the marker", "reveal secret", "api key"),
    *("environment variable", "shell command", "powershell", "curl "),
    *("http://", "https://", "paper_id", "assignment_id"),
    *("overall_recommendation", "submission_endpoint", "json breakout"),
)
_CLAIM_ORDER: Final[Mapping[ClaimImportance, int]] = {
    ClaimImportance.CENTRAL: 0,
    ClaimImportance.SUPPORTING: 1,
    ClaimImportance.BACKGROUND: 2,
}
_STATUS_LABEL: Final[Mapping[FindingStatus, str | None]] = {
    FindingStatus.CANDIDATE: None,
    FindingStatus.CONSENSUS_SUPPORTED: "consensus-supported",
    FindingStatus.MINORITY_SUPPORTED: "minority-supported",
    FindingStatus.CONTESTED: "contested with reduced confidence",
    FindingStatus.UNSUPPORTED_REJECTED: None,
    FindingStatus.UNSUPPORTED_HYPOTHESIS: None,
    FindingStatus.SUBJECTIVE_DIVERGENCE: "subjective divergence with uncertainty",
    FindingStatus.PARSER_UNCERTAIN: "parser-uncertain",
}


def _compact(text: str) -> str:
    return " ".join(text.split())


def _safe_text(text: str, fallback: str, limit: int = 140) -> str:
    visible = "".join(
        character for character in text if category(character) not in {"Cc", "Cf"}
    )
    cleaned = _compact(visible)
    if not cleaned or any(fragment in cleaned.casefold() for fragment in _SUSPICIOUS):
        return fallback
    if len(cleaned) <= limit:
        return cleaned.rstrip(" .")
    shortened = cleaned[:limit].rsplit(" ", maxsplit=1)[0].rstrip(" ,;:")
    return f"{shortened}..."


def _locator(page: int, section: str | None, locator: str | None) -> str:
    parts = [f"page {page}"]
    for prefix, value in (("section ", section), ("", locator)):
        if value is not None:
            safe_value = _safe_text(value, "", 60)
            if safe_value:
                parts.append(f"{prefix}{safe_value}")
    return ", ".join(parts)


def _claim_sections(
    claims: tuple[PaperClaim, ...],
) -> tuple[str, str, tuple[str, ...]]:
    if not claims:
        return (
            _compact(
                """
                Summary. The available claim ledger contains no verified paper claim,
                so a reliable scientific summary cannot be given without inventing
                content. This review therefore limits itself to evidence status and
                the checks needed for a fuller assessment. That limitation concerns
                the available evidence, not the paper's scientific merit, contribution
                value, or likely reception by independent reviewers.
                """
            ),
            _compact(
                """
                Strengths. No claim-grounded scientific strength can be stated from
                the current ledger. A procedural positive is that missing extraction
                remains explicit uncertainty rather than unsupported praise or
                criticism.
                """
            ),
            (),
        )
    ordered = sorted(
        claims,
        key=lambda claim: (_CLAIM_ORDER[claim.importance], claim.claim_id),
    )
    primary = ordered[0]
    primary_text = _safe_text(
        primary.statement,
        "the contribution wording was withheld as non-scientific control text",
    )
    included_ids = (primary.claim_id,)
    if len(ordered) > 1:
        support = _safe_text(
            ordered[1].statement,
            "a supporting claim could not be quoted safely",
        )
        support_sentence = f"It further reports that {support}."
        included_ids = (primary.claim_id, ordered[1].claim_id)
    elif primary.reported_evidence:
        reported = primary.reported_evidence[0]
        support_sentence = _compact(
            f"""
            The paper points to
            {_locator(reported.page, reported.section, reported.locator)} as reported
            support; this is paper-local evidence, not independent verification.
            """
        )
    else:
        support_sentence = _compact(
            """
            No paper-local locator accompanies that assertion, so its support cannot
            be described more strongly from the available material.
            """
        )
    summary = _compact(
        f"""
        Summary. The paper's main claim is that {primary_text}. {support_sentence}
        The assessment stays within extracted claims and cited evidence rather than
        inferring external novelty or unreported results.
        """
    )
    if primary.reported_evidence:
        reported = primary.reported_evidence[0]
        traceability = _compact(
            f"""
            The paper identifies
            {_locator(reported.page, reported.section, reported.locator)}, improving
            traceability between its assertion and reported support.
            """
        )
    else:
        traceability = _compact(
            """
            The claim provides a clear evaluation target, although support remains
            uncertain until a precise paper-local citation is supplied.
            """
        )
    strengths = _compact(
        f"""
        Strengths. A concrete strength is that the main assertion is specific enough
        to inspect: {primary_text}. {traceability}
        """
    )
    return summary, strengths, included_ids


def _select_findings(
    findings: tuple[ReviewFinding, ...],
    calibration: ScoreCalibration,
) -> tuple[SelectedFinding, ...]:
    retained = frozenset(calibration.retained_finding_ids)
    selected = [
        (finding, label)
        for finding in findings
        if finding.finding_id in retained
        and finding.evidence
        and (label := _STATUS_LABEL[finding.status]) is not None
    ]
    selected.sort(
        key=lambda item: (
            -(0.0 if item[0].priority is None else item[0].priority),
            item[0].finding_id,
        )
    )
    return tuple(selected[:3])


def _concern_sections(selected: tuple[SelectedFinding, ...]) -> tuple[str, ...]:
    if not selected:
        return (
            _compact(
                """
                Concerns. No evidence-located retained concern is publishable from the
                supplied findings. This does not establish that the paper has no
                weaknesses; the evidence is too sparse to state one as fact.
                Recommended author check: identify the exact section, table, figure,
                or equation supporting each central claim, and clarify its comparison
                conditions and uncertainty.
                """
            ),
        )
    paragraphs: list[str] = []
    for index, (finding, label) in enumerate(selected, start=1):
        evidence = finding.evidence[0]
        statement = _safe_text(
            finding.statement,
            "Unsafe wording in the retained evidence gap was withheld",
        )
        evidence_summary = _safe_text(
            evidence.summary,
            "The passage could not be quoted safely; only its location is retained",
        )
        check = _safe_text(
            finding.recommended_check or "",
            "Clarify the cited evidence and how it supports the affected claim",
        )
        paragraphs.append(
            _compact(
                f"""
                Concern {index} ({label}; {finding.severity.value} severity;
                {_locator(evidence.page, evidence.section, evidence.locator)}):
                {statement}. The cited passage reports that {evidence_summary}. This has
                {finding.central_claim_impact.value} impact on the target claim and
                {finding.decision_relevance.value} decision relevance. Recommended
                author check: {check}.
                """
            )
        )
    return tuple(paragraphs)


def _score_section(calibration: ScoreCalibration) -> str:
    scores = calibration.scores
    guard = (
        "The deterministic score-comment consistency guards passed"
        if calibration.consistency_guards_passed
        else "The score-comment guards did not pass, so these scores require review"
    )
    return _compact(
        f"""
        Score calibration. Retained evidence leads to soundness {scores.soundness}/4,
        presentation {scores.presentation}/4, significance {scores.significance}/4,
        originality {scores.originality}/4, and an overall recommendation of
        {scores.overall_recommendation}/6. Confidence is {scores.confidence}/5 and
        denotes certainty in this assessment, not paper quality. {guard}; the
        recommendation uses retained evidence rather than averaged reviewer scores.
        """
    )


def build_review_comment(
    claims: tuple[PaperClaim, ...],
    findings: tuple[ReviewFinding, ...],
    calibration: ScoreCalibration,
) -> FormattedReview:
    """Build a deterministic review without republishing untrusted control text."""
    summary, strengths, included_claim_ids = _claim_sections(claims)
    selected = _select_findings(findings, calibration)
    concerns = _concern_sections(selected)
    scope = _compact(
        """
        Scope and next step. This comment does not infer novelty against unchecked
        external literature or treat missing extraction as adverse evidence. Address
        the numbered checks with precise page-local support; if support is unavailable,
        state that limitation so confidence and the recommendation can be recalibrated
        against the same official anchors. Revisit the result if an author response or
        fuller extraction changes the paper-local evidence available for assessment.
        """
    )
    return FormattedReview(
        comment="\n\n".join(
            (summary, strengths, *concerns, _score_section(calibration), scope)
        ),
        trace=CommentInclusionTrace(
            included_claim_ids=included_claim_ids,
            included_finding_ids=tuple(finding.finding_id for finding, _ in selected),
        ),
    )
