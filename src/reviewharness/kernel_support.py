"""Deterministic evidence preparation and kernel artifact persistence."""

import json
import re
import textwrap
import unicodedata
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, TypeAdapter

from reviewharness import (
    artifacts,
    evidence,
    provider_contracts,
    reviewers,
    schemas,
    secure_ingest,
)
from reviewharness.deadline import ReviewMode
from reviewharness.providers import SanitizedEvidencePage, SanitizedPaperEvidence

_HEADING: Final = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:abstract|introduction|method|experiments?|results?|limitations?|conclusions?)\b",
    re.IGNORECASE,
)
_MAX_HEADING: Final = 80
_MAX_PROVIDER_BLOCK_CHARS: Final = 6000
_SENTENCE_BOUNDARY: Final = re.compile(r"(?<=[.!?])\s+")
_JSON: Final[TypeAdapter[artifacts.JsonValue]] = TypeAdapter[artifacts.JsonValue](
    artifacts.JsonValue
)
_EVENTS: Final = (
    "assignment_validated",
    "pdf_ingested",
    "evidence_sanitized",
    "claims_built",
    "reviewers_completed",
    "findings_resolved",
    "scores_calibrated",
    "submission_validated",
)


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    """Sanitized block and provider views from one paper only."""

    blocks: tuple[schemas.PdfBlock, ...]
    provider_evidence: SanitizedPaperEvidence


@dataclass(frozen=True, slots=True)
class ReviewerData:
    """Typed reviewer aggregates without majority-score reduction."""

    outputs: tuple[BaseModel, ...]
    claims: tuple[schemas.PaperClaim, ...]
    findings: tuple[schemas.ReviewFinding, ...]
    proposals: tuple[schemas.ScoreProposal, ...]
    failures: int
    contract_stats: provider_contracts.ProviderContractStats


@dataclass(frozen=True, slots=True)
class KernelTrace:
    """Complete validated evidence needed for required paper artifacts."""

    mode: ReviewMode
    ingest: secure_ingest.SecureIngestResult
    prepared: PreparedEvidence
    reviewer_data: ReviewerData
    claims: tuple[schemas.PaperClaim, ...]
    resolution: evidence.EvidenceResolution
    calibration: schemas.ScoreCalibration
    submission: schemas.ReviewSubmission


def relink_findings(
    findings: Sequence[schemas.ReviewFinding],
    provider_claims: Sequence[schemas.PaperClaim],
    ledger: Sequence[schemas.PaperClaim],
) -> tuple[schemas.ReviewFinding, ...]:
    """Resolve provider claim targets exclusively to normalized ledger IDs."""
    ledger_by_statement: dict[str, set[str]] = {}
    for claim in ledger:
        ledger_by_statement.setdefault(_claim_key(claim.statement), set()).add(
            claim.claim_id
        )
    provider_matches: dict[str, set[str]] = {}
    for claim in provider_claims:
        matches = ledger_by_statement.get(_claim_key(claim.statement), set())
        provider_matches.setdefault(claim.claim_id, set()).update(matches)
    provider_targets = {
        claim_id: next(iter(matches))
        for claim_id, matches in provider_matches.items()
        if len(matches) == 1
    }
    linked: list[schemas.ReviewFinding] = []
    for finding in findings:
        target = finding.target_claim_id
        resolved = provider_targets.get(target) if target is not None else None
        linked.append(finding.model_copy(update={"target_claim_id": resolved}))
    return tuple(linked)


def _claim_key(statement: str) -> str:
    normalized = unicodedata.normalize("NFKC", statement).casefold()
    visible = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    return " ".join(re.findall(r"\w+", visible))


def _model_json(model: BaseModel) -> artifacts.JsonValue:
    return _JSON.validate_json(model.model_dump_json())


def _models_json(models: Sequence[BaseModel]) -> list[artifacts.JsonValue]:
    return [_model_json(model) for model in models]


def prepare_evidence(
    ingest: secure_ingest.SecureIngestResult,
) -> PreparedEvidence:
    """Build page-aware blocks and prefixed provider text with attacks replaced."""
    redactions = {
        finding.location: finding.replacement
        for finding in ingest.security.findings
        if finding.source is secure_ingest.InstructionSource.TEXT
        and finding.quarantined
    }
    blocks: list[schemas.PdfBlock] = []
    pages: list[SanitizedEvidencePage] = []
    for page in ingest.document.pages:
        section: str | None = None
        block_order: list[int] = []
        block_lines: dict[int, list[str]] = {}
        block_sections: dict[int, str | None] = {}
        for line in page.lines:
            locator = line.locator
            location = (
                f"page={locator.page};block={locator.block_index};"
                f"line={locator.line_index}"
            )
            text = redactions.get(location, line.text).strip()
            if not text:
                continue
            if len(text) <= _MAX_HEADING and _HEADING.match(text) is not None:
                section = text
            if locator.block_index not in block_lines:
                block_order.append(locator.block_index)
                block_lines[locator.block_index] = []
                block_sections[locator.block_index] = section
            block_lines[locator.block_index].append(text)
        safe_blocks: list[str] = []
        for block_index in block_order:
            text = " ".join(block_lines[block_index])
            segments = _provider_segments(text)
            for segment_index, segment in enumerate(segments):
                root_id = f"p{page.number}-b{block_index}"
                block_id = (
                    root_id if len(segments) == 1 else f"{root_id}-s{segment_index}"
                )
                blocks.append(
                    schemas.PdfBlock(
                        block_id=block_id,
                        page=page.number,
                        text=segment,
                        section=block_sections[block_index],
                        locator=block_id,
                    )
                )
                safe_blocks.append(f"[{block_id}] {segment}")
        pages.append(
            SanitizedEvidencePage(
                page_number=page.number,
                text="\n".join(safe_blocks),
            )
        )
    notes = tuple(
        f"{finding.category.value}:{finding.finding_id}"
        for finding in ingest.security.findings
    )
    return PreparedEvidence(
        tuple(blocks),
        SanitizedPaperEvidence(
            document_sha256=ingest.document.sha256,
            pages=tuple(pages),
            security_notes=notes,
        ),
    )


def _provider_segments(text: str) -> tuple[str, ...]:
    """Bound provider context at sentence boundaries with a deterministic cap."""
    sentences = _SENTENCE_BOUNDARY.split(text)
    pieces = tuple(
        piece
        for sentence in sentences
        for piece in (
            (sentence,)
            if len(sentence) <= _MAX_PROVIDER_BLOCK_CHARS
            else tuple(
                textwrap.wrap(
                    sentence,
                    width=_MAX_PROVIDER_BLOCK_CHARS,
                    break_long_words=True,
                    break_on_hyphens=False,
                )
            )
        )
        if piece
    )
    segments: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if current and len(candidate) > _MAX_PROVIDER_BLOCK_CHARS:
            segments.append(current)
            current = piece
        else:
            current = candidate
    if current:
        segments.append(current)
    return tuple(segments)


def collect_reviewer_data(
    result: reviewers.ReviewerRunResult,
    blocks: tuple[schemas.PdfBlock, ...],
) -> ReviewerData:
    """Collect full or fast outputs while retaining every candidate finding."""
    outputs: list[BaseModel] = []
    paper_claims: list[schemas.PaperClaim] = []
    findings: list[schemas.ReviewFinding] = []
    proposals: list[schemas.ScoreProposal] = []
    failures = 0
    contract_stats: list[provider_contracts.ProviderContractStats] = []
    for outcome in result.outcomes:
        match outcome:
            case reviewers.ReviewerSuccess(
                output=reviewers.TriLensCandidates() as output
            ):
                outputs.append(output)
                canonical = provider_contracts.canonicalize_provider_candidates(
                    output.claims,
                    output.findings,
                    blocks,
                    outcome.lens.value,
                )
                paper_claims.extend(canonical.claims)
                findings.extend(canonical.findings)
                contract_stats.append(canonical.stats)
                if output.score_proposal is not None:
                    proposals.append(output.score_proposal)
            case reviewers.ReviewerSuccess(
                output=reviewers.SpecialistCandidates() as output
            ):
                outputs.append(output)
                canonical = provider_contracts.canonicalize_provider_candidates(
                    (),
                    output.findings,
                    blocks,
                    outcome.lens.value,
                )
                findings.extend(canonical.findings)
                contract_stats.append(canonical.stats)
            case reviewers.ReviewerFailure():
                failures += 1
            case _:
                assert_never(outcome)
    return ReviewerData(
        tuple(outputs),
        tuple(paper_claims),
        tuple(findings),
        tuple(proposals),
        failures,
        provider_contracts.merge_provider_contract_stats(contract_stats),
    )


def _write_events(
    store: artifacts.ArtifactStore,
    paper_id: str,
    mode: ReviewMode,
) -> None:
    text = "".join(
        json.dumps({"event": event, "mode": mode.value, "paper_id": paper_id}) + "\n"
        for event in _EVENTS
    )
    record = store.write_text(paper_id, "events", text)
    _ = record.path.replace(store.paper_directory(paper_id) / "events.jsonl")
    with suppress(FileNotFoundError):
        record.manifest_path.unlink()


def persist_trace(
    output_dir: Path,
    assignment: schemas.TrustedAssignment,
    trace: KernelTrace,
) -> None:
    """Atomically persist all required per-paper evidence artifacts."""
    store = artifacts.ArtifactStore(output_dir)
    payloads: tuple[tuple[str, artifacts.JsonValue], ...] = (
        ("assignment", _model_json(assignment)),
        (
            "pdf_hash",
            {
                "sha256": trace.ingest.document.sha256,
                "page_count": trace.ingest.document.page_count,
            },
        ),
        ("security_scan", _model_json(trace.ingest.security)),
        (
            "parsed_structure",
            {
                "page_count": trace.ingest.document.page_count,
                "blocks": _models_json(trace.prepared.blocks),
            },
        ),
        ("claim_ledger", _models_json(trace.claims)),
        (
            "reviewer_outputs",
            {
                "outputs": _models_json(trace.reviewer_data.outputs),
                "failure_count": trace.reviewer_data.failures,
                "provider_contract": _model_json(trace.reviewer_data.contract_stats),
            },
        ),
        ("normalized_findings", _models_json(trace.resolution.retained)),
        ("rejected_findings", _models_json(trace.resolution.rejected)),
        ("score_trace", _model_json(trace.calibration)),
        ("review", _model_json(trace.submission)),
    )
    for stage, payload in payloads:
        _ = store.write_json(assignment.paper_id, stage, payload)
    _write_events(store, assignment.paper_id, trace.mode)
