"""Deterministic prompt-injection classification and safe quarantine views."""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum, unique
from hashlib import sha256
from typing import Final, assert_never

from .schemas import InjectionClassification, SecurityDetection, SecurityReport


@unique
class SourceOrigin(StrEnum):
    """Origin of an inert paper-derived text span."""

    VISIBLE_TEXT = "visible_text"
    HIDDEN_TEXT = "hidden_text"
    METADATA = "metadata"
    ANNOTATION = "annotation"
    LINK = "link"
    ATTACHMENT = "attachment"
    ACTIVE_CONTENT = "active_content"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Paper-derived text and its immutable source location."""

    page: int | None
    block_id: str | None
    locator: str | None
    text: str
    origin: SourceOrigin = SourceOrigin.VISIBLE_TEXT


@dataclass(frozen=True, slots=True)
class InjectionScan:
    """Inputs for one document-local injection scan."""

    document_sha256: str
    sources: tuple[SourceSpan, ...]
    sanitization_limited_review: bool = False


type SafeSpan = SourceSpan


@dataclass(frozen=True, slots=True)
class QuarantinedSpan:
    """Provenance record that deliberately excludes raw suspicious text."""

    page: int | None
    block_id: str | None
    locator: str | None
    source_sha256: str
    detection: SecurityDetection
    calibrator_safe_summary: str


@dataclass(frozen=True, slots=True)
class QuarantineResult:
    """Aggregate report plus placeholder and removal invariance variants."""

    report: SecurityReport
    quarantined_spans: tuple[QuarantinedSpan, ...]
    placeholder_variant: tuple[SafeSpan, ...]
    removal_variant: tuple[SafeSpan, ...]
    calibrator_safe_summary: str


_ZERO_WIDTH: Final = frozenset({"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"})
_QUOTE_CHARACTERS: Final = frozenset({'"', "'", "\u201c", "\u201d", "\u2018", "\u2019"})


def _pattern(fragments: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("".join(fragments))


_BENIGN_CONTEXT: Final = _pattern(
    (
        r"\b(?:we (?:evaluate|study|analyze)|(?:paper|benchmark|study) ",
        r"(?:quotes?|evaluates?|studies?|analyzes?)|quoted (?:example|attack)|",
        r"research data|experimental evidence|adversarial research)\b",
    ),
)
_BENIGN_DISCLAIMER: Final = _pattern(
    (
        r"\b(?:not an instruction|not a reviewer command|quoted (?:example|attack)|",
        r"research data|experimental evidence)\b",
    ),
)
_CANARY: Final = _pattern(
    (
        r"\b(?:include|print(?:ing)?|repeat|emit|output|write)\b.{0,80}",
        r"(?:marker|canary|verification phrase|exact phrase)",
        r"|(?:marker|canary|verification phrase|exact phrase).{0,80}",
        r"\b(?:include|print(?:ing)?|repeat|emit|output|write)\b",
    ),
)
_MANIPULATIVE: Final = _pattern(
    (
        r"\b(?:ignore|override|disregard)\b.{0,48}",
        r"\b(?:prior |previous |official )?(?:instruction|rubric|review|system)\w*\b",
        r"|\b(?:assign|give|set|output|force)\b.{0,48}",
        r"\b(?:score|rating|recommendation|strong accept|accept)\b",
        r"|\b(?:omit|suppress)\b.{0,32}\b(?:weakness|limitation|criticism|flaw)\w*\b",
        r"|\b(?:do not|don't|never)\b.{0,64}",
        r"\b(?:weakness|limitation|criticism|flaw)\w*\b",
        r"|\b(?:system|developer|administrator|conference chair|program chair|",
        r"meta-reviewer)\b.{0,64}\b(?:message|instruction|directive|rubric|",
        r"accept|score|override)\b",
        r"|\b(?:replac(?:e|ement)|override|ignore|alternate|new)\b.{0,32}",
        r"\b(?:official )?rubric\b",
        r"|(?:\}\s*,|\]\s*,).{0,64}\"(?:overall(?:_recommendation)?|score|",
        r"comment|paper_id)\"",
        r"|\b(?:close|break|extend)\b.{0,24}\bjson\b.{0,64}",
        r"\b(?:admin_override|second object|field|object)\b",
        r"|\b(?:print|reveal|return|show|exfiltrate|include)\b.{0,48}",
        r"\b(?:api[_ -]?key|secret|credential|environment variable|token)\w*\b",
        r"|\b(?:shell|bash|powershell|cmd|curl|wget|use (?:the )?tool|",
        r"invoke (?:a )?tool|visit https?://|open https?://)\b",
        r"|\b(?:lower|raise|set|modify|poison|influence|change|copy)\b.{0,64}",
        r"\b(?:other|later|next|previous|all)\s+papers?'?s?\s+(?:review|score)\w*\b",
        r"|\b(?:replace|change|override|redirect)\b.{0,32}",
        r"\b(?:paper[_ ]?id|submission|endpoint|routing)\b",
    ),
)
_SUSPICIOUS_METADATA: Final = re.compile(
    r"\b(?:invisible|reviewer|instruction|system|chair|score|rubric|secret|tool)\b",
)


def _normalized(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    visible = "".join(
        character for character in normalized if character not in _ZERO_WIDTH
    )
    return " ".join(visible.casefold().split())


def _is_benign_quoted_example(raw: str, normalized: str) -> bool:
    explicitly_quoted = (
        any(character in raw for character in _QUOTE_CHARACTERS)
        or "quoted attack example" in normalized
    )
    return (
        explicitly_quoted
        and _BENIGN_CONTEXT.search(normalized) is not None
        and _BENIGN_DISCLAIMER.search(normalized) is not None
        and (
            _MANIPULATIVE.search(normalized) is not None
            or _CANARY.search(normalized) is not None
        )
    )


def classify_source(source: SourceSpan) -> InjectionClassification | None:
    """Classify one inert source span without executing or obeying its text."""
    normalized = _normalized(source.text)
    if _is_benign_quoted_example(source.text, normalized):
        return InjectionClassification.BENIGN_QUOTED_EXAMPLE
    if _MANIPULATIVE.search(normalized) is not None:
        return InjectionClassification.MANIPULATIVE_INSTRUCTION
    if _CANARY.search(normalized) is not None:
        return InjectionClassification.REVIEWER_DETECTION_CANARY
    has_zero_width = any(character in source.text for character in _ZERO_WIDTH)
    structural_signal = source.origin in {
        SourceOrigin.HIDDEN_TEXT,
        SourceOrigin.ATTACHMENT,
        SourceOrigin.ACTIVE_CONTENT,
    }
    metadata_signal = (
        source.origin is SourceOrigin.METADATA
        and _SUSPICIOUS_METADATA.search(normalized) is not None
    )
    if has_zero_width or structural_signal or metadata_signal:
        return InjectionClassification.UNCERTAIN_INSTRUCTION
    return None


def _safe_summary(
    classification: InjectionClassification,
    source: SourceSpan,
    source_sha256: str,
) -> str:
    match classification:
        case InjectionClassification.MANIPULATIVE_INSTRUCTION:
            description = "Document-borne manipulative instruction quarantined."
        case InjectionClassification.REVIEWER_DETECTION_CANARY:
            description = "Reviewer-detection marker request quarantined."
        case InjectionClassification.BENIGN_QUOTED_EXAMPLE:
            description = "Quoted security research example isolated as data."
        case InjectionClassification.UNCERTAIN_INSTRUCTION:
            description = "Potential document-borne instruction signal quarantined."
        case _:
            assert_never(classification)
    page = "not-applicable" if source.page is None else str(source.page)
    return f"{description} Source page {page}; span sha256 {source_sha256}."


def _safe_span(source: SourceSpan, text: str) -> SafeSpan:
    return SourceSpan(source.page, source.block_id, source.locator, text, source.origin)


def quarantine_sources(scan: InjectionScan) -> QuarantineResult:
    """Produce safe placeholder/removal variants and a raw-text-free report."""
    detections: list[SecurityDetection] = []
    records: list[QuarantinedSpan] = []
    placeholder_variant: list[SafeSpan] = []
    removal_variant: list[SafeSpan] = []
    for source in scan.sources:
        classification = classify_source(source)
        match classification:
            case None:
                safe_span = _safe_span(source, source.text)
                placeholder_variant.append(safe_span)
                removal_variant.append(safe_span)
            case (
                InjectionClassification.MANIPULATIVE_INSTRUCTION
                | InjectionClassification.REVIEWER_DETECTION_CANARY
                | InjectionClassification.BENIGN_QUOTED_EXAMPLE
                | InjectionClassification.UNCERTAIN_INSTRUCTION
            ):
                span_sha256 = sha256(source.text.encode("utf-8")).hexdigest()
                summary = _safe_summary(classification, source, span_sha256)
                detection = SecurityDetection(
                    classification=classification,
                    page=source.page,
                    block_id=source.block_id,
                    summary=summary,
                    quarantined=(
                        classification
                        is not InjectionClassification.BENIGN_QUOTED_EXAMPLE
                    ),
                )
                detections.append(detection)
                match classification:
                    case InjectionClassification.BENIGN_QUOTED_EXAMPLE:
                        safe_span = _safe_span(source, source.text)
                        placeholder_variant.append(safe_span)
                        removal_variant.append(safe_span)
                    case (
                        InjectionClassification.MANIPULATIVE_INSTRUCTION
                        | InjectionClassification.REVIEWER_DETECTION_CANARY
                        | InjectionClassification.UNCERTAIN_INSTRUCTION
                    ):
                        records.append(
                            QuarantinedSpan(
                                page=source.page,
                                block_id=source.block_id,
                                locator=source.locator,
                                source_sha256=span_sha256,
                                detection=detection,
                                calibrator_safe_summary=summary,
                            ),
                        )
                        placeholder_variant.append(_safe_span(source, f"[{summary}]"))
                    case _:
                        assert_never(classification)
            case _:
                assert_never(classification)
    origins = frozenset(source.origin for source in scan.sources)
    report = SecurityReport(
        document_sha256=scan.document_sha256,
        detections=tuple(detections),
        active_content_detected=SourceOrigin.ACTIVE_CONTENT in origins,
        annotations_detected=SourceOrigin.ANNOTATION in origins,
        attachments_detected=SourceOrigin.ATTACHMENT in origins,
        links_detected=SourceOrigin.LINK in origins,
        sanitization_limited_review=scan.sanitization_limited_review,
    )
    safe_summary = (
        "No document-borne instruction signal was detected."
        if not detections
        else " ".join(detection.summary for detection in detections)
    )
    return QuarantineResult(
        report=report,
        quarantined_spans=tuple(records),
        placeholder_variant=tuple(placeholder_variant),
        removal_variant=tuple(removal_variant),
        calibrator_safe_summary=safe_summary,
    )
