"""Deterministic security ingest for untrusted PDF evidence."""

import re
from dataclasses import dataclass
from enum import StrEnum, unique
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .parser import ParsedDocument, TextLocator, parse_pdf
from .schemas import InjectionClassification

InstructionCategory = InjectionClassification

CANARY_PATTERN: Final = re.compile(
    (
        r"\b(?:marker phrase|canary|RH_[A-Z0-9_]+)\b"
        r"|\b(?:include|print|emit|output|write)\b.{0,80}\b(?:exact )?phrase\b"
    ),
    re.IGNORECASE,
)
ZERO_WIDTH_PATTERN: Final = re.compile("[\u200b\u200c\u200d\ufeff]")
TINY_TEXT_POINTS: Final = 2.0
TRANSPARENT_OPACITY: Final = 0.05
BENIGN_CONTEXT: Final = (
    "quoted attack example",
    "example attack",
    "experimental evidence",
    "not a reviewer command",
)
AUTHORITY_FRAGMENTS: Final = (
    "system message",
    "to reviewer",
    "reviewer instruction",
    "chair:",
)
SCORE_FRAGMENTS: Final = ("strong accept", "give this paper", "set score", "score to 6")
CONTROL_FRAGMENTS: Final = (
    "ignore the official rubric",
    "ignore prior instructions",
    "ignore previous instructions",
    "omit all weaknesses",
    "do not mention any weaknesses",
    "skip criticism",
)
SECRET_FRAGMENTS: Final = (
    "api key",
    "reveal secrets",
    "leak environment",
    "environment variables",
)
QUARANTINED_CATEGORIES: Final = frozenset(
    {
        InjectionClassification.MANIPULATIVE_INSTRUCTION,
        InjectionClassification.REVIEWER_DETECTION_CANARY,
        InjectionClassification.UNCERTAIN_INSTRUCTION,
    }
)


@unique
class InstructionSource(StrEnum):
    """Untrusted PDF channel containing a classified span."""

    TEXT = "text"
    METADATA = "metadata"
    LINK = "link"
    ANNOTATION = "annotation"
    ATTACHMENT = "attachment"


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class SecurityFinding(_FrozenModel):
    """Internal evidence record retaining raw text outside reviewer input."""

    finding_id: str = Field(pattern=r"^SI-[0-9a-f]{12}$")
    category: InjectionClassification
    source: InstructionSource
    location: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    replacement: str = Field(min_length=1)
    reasons: tuple[str, ...]
    quarantined: bool


class SecurityScan(_FrozenModel):
    """Deterministic scan metadata with an explicit no-penalty contract."""

    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: tuple[SecurityFinding, ...]
    scientific_penalty_applied: Literal[False] = False
    annotations_detected: bool
    attachments_detected: bool
    links_detected: bool


class SecureIngestResult(_FrozenModel):
    """Parsed evidence, internal scan, and authority-neutralized reviewer text."""

    document: ParsedDocument
    security: SecurityScan
    sanitized_text: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: InstructionSource
    location: str
    text: str
    reasons: tuple[str, ...] = ()


def _location(locator: TextLocator) -> str:
    return f"page={locator.page};block={locator.block_index};line={locator.line_index}"


def _reasons(text: str, structural: tuple[str, ...]) -> tuple[str, ...]:
    normalized = text.casefold()
    found = list(structural)
    if CANARY_PATTERN.search(text):
        found.append("canary_marker")
    if any(fragment in normalized for fragment in AUTHORITY_FRAGMENTS):
        found.append("fake_authority")
    if any(fragment in normalized for fragment in SCORE_FRAGMENTS):
        found.append("score_steering")
    if any(fragment in normalized for fragment in CONTROL_FRAGMENTS):
        found.append("control_override")
    if any(fragment in normalized for fragment in SECRET_FRAGMENTS):
        found.append("secret_request")
    if ZERO_WIDTH_PATTERN.search(text):
        found.append("zero_width_text")
    return tuple(sorted(set(found)))


def _category(text: str, reasons: tuple[str, ...]) -> InjectionClassification | None:
    normalized = text.casefold()
    if any(fragment in normalized for fragment in BENIGN_CONTEXT) and reasons:
        return InjectionClassification.BENIGN_QUOTED_EXAMPLE
    if "canary_marker" in reasons:
        return InjectionClassification.REVIEWER_DETECTION_CANARY
    lexical = {
        "fake_authority",
        "score_steering",
        "control_override",
        "secret_request",
        "zero_width_text",
    }
    if lexical.intersection(reasons):
        return InjectionClassification.MANIPULATIVE_INSTRUCTION
    if reasons:
        return InjectionClassification.UNCERTAIN_INSTRUCTION
    return None


def _finding(document_hash: str, candidate: _Candidate) -> SecurityFinding | None:
    if not candidate.text.strip():
        return None
    reasons = _reasons(candidate.text, candidate.reasons)
    category = _category(candidate.text, reasons)
    if category is None:
        return None
    digest = sha256(
        (
            f"{document_hash}|{candidate.source.value}|"
            f"{candidate.location}|{candidate.text}"
        ).encode()
    ).hexdigest()[:12]
    finding_id = f"SI-{digest}"
    quarantined = category in QUARANTINED_CATEGORIES
    replacement = (
        f"[QUARANTINED_UNTRUSTED_INSTRUCTION:{finding_id}]"
        if quarantined
        else candidate.text
    )
    return SecurityFinding(
        finding_id=finding_id,
        category=category,
        source=candidate.source,
        location=candidate.location,
        source_text=candidate.text,
        replacement=replacement,
        reasons=reasons,
        quarantined=quarantined,
    )


def _candidates(document: ParsedDocument) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    for page in document.pages:
        for line in page.lines:
            structural: list[str] = []
            if any(span.font_size < TINY_TEXT_POINTS for span in line.spans):
                structural.append("tiny_text")
            if any(span.opacity <= TRANSPARENT_OPACITY for span in line.spans):
                structural.append("transparent_text")
            x0, y0, x1, y1 = line.bbox
            if x0 < 0 or y0 < 0 or x1 > page.width or y1 > page.height:
                structural.append("off_page_text")
            candidates.append(
                _Candidate(
                    source=InstructionSource.TEXT,
                    location=_location(line.locator),
                    text=line.text,
                    reasons=tuple(structural),
                )
            )
    metadata = document.metadata
    for name, text in (
        ("title", metadata.title),
        ("author", metadata.author),
        ("subject", metadata.subject),
        ("keywords", metadata.keywords),
        ("creator", metadata.creator),
        ("producer", metadata.producer),
    ):
        if text:
            candidates.append(
                _Candidate(InstructionSource.METADATA, f"metadata:{name}", text)
            )
    candidates.extend(
        _Candidate(
            InstructionSource.LINK,
            f"link:page={link.page}",
            link.uri,
            ("external_link",),
        )
        for link in document.links
    )
    candidates.extend(
        _Candidate(
            InstructionSource.ANNOTATION,
            f"annotation:page={annotation.page}",
            annotation.content,
            ("annotation",),
        )
        for annotation in document.annotations
        if annotation.content
    )
    candidates.extend(
        _Candidate(
            InstructionSource.ATTACHMENT,
            f"attachment:{item.name}",
            item.content_preview or item.name,
            ("embedded_file",),
        )
        for item in document.embedded_files
    )
    return tuple(candidates)


def ingest_pdf(path: Path) -> SecureIngestResult:
    """Parse and quarantine instruction authority while retaining raw evidence."""
    document = parse_pdf(path)
    findings = tuple(
        finding
        for candidate in _candidates(document)
        if (finding := _finding(document.sha256, candidate)) is not None
    )
    redactions = {
        finding.location: finding.replacement
        for finding in findings
        if finding.source is InstructionSource.TEXT and finding.quarantined
    }
    pages = tuple(
        "\n".join(
            (
                f"--- Page {page.number} ---",
                *(
                    redactions.get(_location(line.locator), line.text)
                    for line in page.lines
                ),
            )
        )
        for page in document.pages
    )
    return SecureIngestResult(
        document=document,
        security=SecurityScan(
            document_sha256=document.sha256,
            findings=findings,
            annotations_detected=bool(document.annotations),
            attachments_detected=bool(document.embedded_files),
            links_detected=bool(document.links),
        ),
        sanitized_text="\n\n".join(pages),
    )
