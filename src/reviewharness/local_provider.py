"""Deterministic capability-free reviewer for local execution and evaluation."""

import re
from dataclasses import dataclass
from typing import Final, final

from anyio.lowlevel import checkpoint

from .provider_contracts import (
    ProviderClaim,
    ProviderClaimEvidence,
    ProviderFinding,
    ProviderFindingEvidence,
)
from .providers import (
    ProviderCallError,
    ReviewerRequest,
    ReviewerResponse,
)
from .reviewers import (
    ReviewerLens,
    SpecialistCandidates,
    TriLensCandidates,
)
from .schemas import (
    ClaimImportance,
    ClaimType,
    DecisionRelevance,
    FindingSeverity,
    JudgmentType,
    ReviewScores,
    ScoreProposal,
)


@dataclass(frozen=True, slots=True)
class _Sentence:
    page: int
    locator: str
    block_id: str | None
    text: str


_LINE_PREFIX: Final = re.compile(
    r"^\[(?P<locator>p(?P<page>\d+)-b\d+(?:-s\d+)?)\]\s*",
)
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+")
_SCIENCE_SIGNAL: Final = re.compile(
    r"""\b(?:we|paper|study|method|approach|model|classifier|experiment|
    evaluation|results?|table|ablation|baseline|datasets?|accuracy|detector|
    analysis|claim|benchmark)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_UNTRUSTED_INSTRUCTION: Final = re.compile(
    r"""\[quarantined|\b(?:system.message|ignore.(?:previous.|official.)?instructions?|
    strong.accept|output.(?:the.)?marker|marker.phrase|canary|secret.request|
    environment.variable|shell.command|conference.chair|reviewer.command)\b""",
    re.IGNORECASE | re.VERBOSE,
)
_UNCERTAINTY_NOTES: Final = (
    "Assessment uses only supplied sanitized evidence; external novelty is unverified.",
)
type _Rule = tuple[str, str]
_RULES: Final[tuple[_Rule, ...]] = (
    (
        "baseline_coverage",
        r"\b(?:no baselines?|without baselines?|compare\w* against no baselines?)\b",
    ),
    (
        "ablation_coverage",
        r"\b(?:no|without) ablations?\b|\bomit\w*[^.]{0,50}\bablations?\b",
    ),
    (
        "statistical_reporting",
        r"\b(?:no|without|omit\w*)\b[^.]{0,80}\b(?:error bars?|variance|CIs?)\b",
    ),
    (
        "evaluation_scope",
        r"\b(?:one|single) (?:public )?datasets?\b|\b(?:one|two) datasets? only\b",
    ),
    (
        "single_seed_evaluation",
        r"\b(?:one|single) (?:fixed )?seed\b",
    ),
)
_MIN_SENTENCE_LENGTH: Final = 20
_MULTIPLE_GAPS: Final = 2


@final
class LocalHeuristicProvider:
    """Produce conservative review JSON from sanitized evidence without capabilities."""

    async def review(self, request: ReviewerRequest) -> ReviewerResponse:
        """Return the strict named reviewer schema using deterministic local rules."""
        await checkpoint()
        kind = _schema_kind(request.output_schema.name)
        sentences = _sentences(request)
        if kind is ReviewerLens.TRI_LENS:
            raw_output = _tri_lens_output(sentences).model_dump_json()
        else:
            raw_output = _specialist_output(kind, sentences).model_dump_json()
        return ReviewerResponse(raw_output=raw_output)


def _schema_kind(name: str) -> ReviewerLens:
    lens = {
        "method_findings": ReviewerLens.METHOD,
        "reviewer_findings": ReviewerLens.METHOD,
        "evidence_findings": ReviewerLens.EVIDENCE,
        "impact_findings": ReviewerLens.IMPACT,
        "tri_lens_review": ReviewerLens.TRI_LENS,
    }.get(name)
    if lens is None:
        detail = f"unsupported output schema: {name}"
        raise ProviderCallError(detail)
    return lens


def _sentences(request: ReviewerRequest) -> tuple[_Sentence, ...]:
    sentences: list[_Sentence] = []
    for page in request.sanitized_evidence.pages:
        safe_index = 0
        for raw_line in page.text.splitlines():
            prefix = _LINE_PREFIX.match(raw_line)
            if prefix is not None and int(prefix.group("page")) == page.page_number:
                block_id = prefix.group("locator")
                line = raw_line[prefix.end() :]
            else:
                block_id = None
                line = raw_line
            for chunk in _SENTENCE_BREAK.split(line):
                text = " ".join(chunk.split())
                text = text.removeprefix("Abstract:").removeprefix("Summary:").strip()
                if (
                    len(text) >= _MIN_SENTENCE_LENGTH
                    and text.endswith((".", "!", "?"))
                    and _SCIENCE_SIGNAL.search(text) is not None
                    and _UNTRUSTED_INSTRUCTION.search(text) is None
                ):
                    safe_index += 1
                    locator = block_id or f"sanitized sentence {safe_index}"
                    sentences.append(
                        _Sentence(page.page_number, locator, block_id, text)
                    )
    return tuple(sentences)


def _claims(sentences: tuple[_Sentence, ...]) -> tuple[ProviderClaim, ...]:
    supported = tuple(
        sentence for sentence in sentences if sentence.block_id is not None
    )
    claims: list[ProviderClaim] = []
    for index, sentence in enumerate(supported[:2], start=1):
        if sentence.block_id is None:
            continue
        claims.append(
            ProviderClaim(
                claim_id=f"C{index}",
                statement=sentence.text,
                importance=ClaimImportance.CENTRAL
                if index == 1
                else ClaimImportance.SUPPORTING,
                claim_type=ClaimType.EMPIRICAL,
                reported_evidence=(
                    ProviderClaimEvidence(
                        page=sentence.page,
                        block_id=sentence.block_id,
                        quote=sentence.text,
                    ),
                ),
            ),
        )
    return tuple(claims)


def _findings(
    sentences: tuple[_Sentence, ...], reviewer: str
) -> tuple[ProviderFinding, ...]:
    findings: list[ProviderFinding] = []
    for category, pattern in _RULES:
        source = next(
            (
                item
                for item in sentences
                if re.search(pattern, item.text, re.IGNORECASE)
            ),
            None,
        )
        if source is None or source.block_id is None:
            continue
        findings.append(
            ProviderFinding(
                finding_id=f"LOCAL-{category.upper()}-{source.page}",
                category=category,
                judgment_type=JudgmentType.MIXED,
                severity=FindingSeverity.MAJOR,
                statement=(
                    "The paper explicitly states a "
                    + category.replace("_", " ")
                    + " limitation."
                ),
                target_claim_id="C1",
                evidence=(
                    ProviderFindingEvidence(
                        page=source.page,
                        block_id=source.block_id,
                        quote=source.text,
                    ),
                ),
                decision_relevance=DecisionRelevance.HIGH,
                recommended_check=(
                    "Address the documented "
                    + category.replace("_", " ")
                    + " limitation with targeted evidence."
                ),
                confidence=0.95,
            ),
        )
    _ = reviewer
    return tuple(findings)


def _specialist_output(
    kind: ReviewerLens,
    sentences: tuple[_Sentence, ...],
) -> SpecialistCandidates:
    findings = _findings(sentences, "local_" + kind.value)
    return SpecialistCandidates(findings=findings, uncertainty_notes=_UNCERTAINTY_NOTES)


def _tri_lens_output(sentences: tuple[_Sentence, ...]) -> TriLensCandidates:
    claims = _claims(sentences)
    findings = _findings(sentences, "local_tri_lens")
    summary = " ".join(sentence.text for sentence in sentences[:2]) or (
        "Sanitized evidence lacks enough scientific prose for a substantive summary."
    )
    strengths = (
        ("The paper states a concrete, testable scientific claim.",) if claims else ()
    )
    category_count = len({finding.category for finding in findings})
    severe_gap = any(
        finding.category == "single_seed_evaluation" for finding in findings
    )
    if severe_gap or category_count >= _MULTIPLE_GAPS:
        score_values = (2, 3, 2, 3, 2, 4)
    elif category_count == 1:
        score_values = (3, 3, 2, 3, 3, 4)
    else:
        score_values = (3, 3, 3, 3, 4, 3)
    scores = ReviewScores(
        soundness=score_values[0],
        presentation=score_values[1],
        significance=score_values[2],
        originality=score_values[3],
        overall_recommendation=score_values[4],
        confidence=score_values[5],
    )
    proposal = ScoreProposal(
        reviewer="local_tri_lens",
        scores=scores,
        rationale="Scores use explicit paper evidence and conservative rubric anchors.",
        finding_ids=tuple(finding.finding_id for finding in findings),
    )
    return TriLensCandidates(
        summary=summary,
        claims=claims,
        strengths=strengths,
        findings=findings,
        score_proposal=proposal,
        uncertainty_notes=_UNCERTAINTY_NOTES,
    )
