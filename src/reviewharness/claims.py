"""Deterministic claim-ledger construction over parser-owned PDF blocks."""

import re
import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher
from hashlib import blake2s
from typing import Final

from reviewharness.schemas import (
    ClaimImportance,
    ClaimLocator,
    ClaimType,
    PaperClaim,
    PdfBlock,
)

_SENTENCE_RE: Final = re.compile(r"(?<=[.!?])\s+")
_CLAIM_LENGTH_RANGE: Final = range(20, 601)
_SEQUENCE_DUPLICATE_THRESHOLD: Final = 0.88
_HIGH_LEVEL_RE: Final = re.compile(
    r"\b(?:abstract|introduction|conclusions?)\b",
    re.IGNORECASE,
)
_HEADING_RE: Final = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:abstract|introduction|conclusions?)\W*",
    re.IGNORECASE,
)
_INSTRUCTION_RE: Final = re.compile(
    r"""
    \b(?:reviewer\s+(?:must|should)|review\s+agent\s+must|
    ignore\s+(?:previous|the\s+rubric)|(?:assign|give).{0,24}\bscore|
    (?:output|include)\s+(?:marker|canary)|(?:reveal|print).{0,20}\bsecret|
    api\s+key|(?:run|execute)\s+(?:shell|command)|system\s+message|
    conference\s+chair\s+message|suppress\s+all\s+weaknesses)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_CLAIM_SIGNAL_RE: Final = re.compile(
    r"""
    \b(?:we\s+(?:propose|present|introduce|develop|show|demonstrate|find|
    establish|argue|evaluate)|this\s+(?:paper|work|study)\s+(?:proposes|
    presents|introduces|investigates|evaluates)|our\s+(?:method|model|approach|
    framework|algorithm|dataset|system)|(?:results?|experiments?)\s+(?:show|
    shows|demonstrate|demonstrates|indicate))\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TYPE_KEYWORDS: Final[tuple[tuple[ClaimType, tuple[str, ...]], ...]] = (
    (ClaimType.DATASET, ("dataset",)),
    (ClaimType.THEORETICAL, ("theorem", "proof", "bound")),
    (ClaimType.SYSTEMS, ("system", "runtime", "throughput")),
    (ClaimType.METHODOLOGICAL, ("method", "model", "approach", "algorithm")),
    (ClaimType.EMPIRICAL, ("experiment", "result", "accuracy", "improve")),
    (ClaimType.ANALYSIS, ("analysis", "investigate", "study")),
    (ClaimType.POSITION, ("position", "argue")),
)
_IMPORTANCE_RANKS: Final = {
    ClaimImportance.CENTRAL: 0,
    ClaimImportance.SUPPORTING: 1,
    ClaimImportance.BACKGROUND: 2,
}


def normalize_claim_candidates(
    candidates: Sequence[PaperClaim],
    blocks: Sequence[PdfBlock],
) -> tuple[PaperClaim, ...]:
    """Normalize provider claims against parser-owned locators."""
    canonical = tuple(
        claim
        for candidate in candidates
        if (claim := _canonicalize_candidate(candidate, blocks)) is not None
    )
    groups: list[list[PaperClaim]] = []
    for claim in sorted(canonical, key=lambda item: _normalized(item.statement)):
        for group in groups:
            if any(_near_duplicate(claim, member) for member in group):
                group.append(claim)
                break
        else:
            groups.append([claim])
    merged = tuple(_merge_group(group) for group in groups)
    promoted = _promote_central_claim(merged, blocks)
    return tuple(sorted(promoted, key=_claim_sort_key))


def heuristic_claims(blocks: Sequence[PdfBlock]) -> tuple[PaperClaim, ...]:
    """Extract conservative claims from parser-owned high-level blocks."""
    candidates: list[PaperClaim] = []
    for block in sorted(blocks, key=lambda item: (item.page, item.block_id)):
        if not _is_high_level_block(block):
            continue
        statement = _extract_scientific_statement(block.text)
        if statement is None:
            continue
        candidates.append(
            PaperClaim(
                claim_id="heuristic",
                statement=statement,
                importance=ClaimImportance.SUPPORTING,
                claim_type=_classify_claim(statement),
                reported_evidence=(_block_locator(block),),
            )
        )
    return normalize_claim_candidates(candidates, blocks)


def build_claim_ledger(
    candidates: Sequence[PaperClaim],
    blocks: Sequence[PdfBlock],
) -> tuple[PaperClaim, ...]:
    """Build a normalized provider ledger or a deterministic fallback."""
    normalized = normalize_claim_candidates(candidates, blocks)
    return normalized or heuristic_claims(blocks)


def _canonicalize_candidate(
    candidate: PaperClaim,
    blocks: Sequence[PdfBlock],
) -> PaperClaim | None:
    statement = _clean(candidate.statement)
    if not statement or _INSTRUCTION_RE.search(statement) is not None:
        return None
    locators = tuple(
        locator
        for reported in candidate.reported_evidence
        if (locator := _canonical_locator(reported, blocks)) is not None
    )
    unique_locators = _unique_locators(locators)
    if not unique_locators:
        return None
    return PaperClaim(
        claim_id=candidate.claim_id,
        statement=statement,
        importance=candidate.importance,
        claim_type=candidate.claim_type,
        reported_evidence=unique_locators,
    )


def _canonical_locator(
    locator: ClaimLocator,
    blocks: Sequence[PdfBlock],
) -> ClaimLocator | None:
    matches = tuple(block for block in blocks if _matches_locator(locator, block))
    if len(matches) != 1:
        return None
    return _block_locator(matches[0])


def _matches_locator(locator: ClaimLocator, block: PdfBlock) -> bool:
    identity_exists = any(
        item is not None
        for item in (locator.block_id, locator.section, locator.locator)
    )
    return (
        locator.page == block.page
        and identity_exists
        and _label_matches(locator.block_id, block.block_id)
        and _label_matches(locator.section, block.section)
        and _label_matches(locator.locator, block.locator)
    )


def _label_matches(expected: str | None, actual: str | None) -> bool:
    return expected is None or (
        actual is not None and _normalized(expected) == _normalized(actual)
    )


def _unique_locators(locators: Sequence[ClaimLocator]) -> tuple[ClaimLocator, ...]:
    return tuple(sorted(set(locators), key=_locator_key))


def _merge_group(group: Sequence[PaperClaim]) -> PaperClaim:
    representative = max(group, key=lambda item: item.statement.casefold())
    importance = min(
        (item.importance for item in group), key=_IMPORTANCE_RANKS.__getitem__
    )
    locators = _unique_locators(
        tuple(locator for item in group for locator in item.reported_evidence)
    )
    return PaperClaim(
        claim_id=_stable_claim_id(representative.statement),
        statement=representative.statement,
        importance=importance,
        claim_type=representative.claim_type,
        reported_evidence=locators,
    )


def _promote_central_claim(
    claims: Sequence[PaperClaim],
    blocks: Sequence[PdfBlock],
) -> tuple[PaperClaim, ...]:
    if any(claim.importance is ClaimImportance.CENTRAL for claim in claims):
        return tuple(claims)
    high_level_ids = {block.block_id for block in blocks if _is_high_level_block(block)}
    eligible = tuple(
        claim
        for claim in claims
        if any(
            locator.block_id in high_level_ids for locator in claim.reported_evidence
        )
    )
    if not eligible:
        return tuple(claims)
    selected = min(eligible, key=_claim_sort_key)
    return tuple(
        claim.model_copy(update={"importance": ClaimImportance.CENTRAL})
        if claim.claim_id == selected.claim_id
        else claim
        for claim in claims
    )


def _extract_scientific_statement(text: str) -> str | None:
    for raw_sentence in _SENTENCE_RE.split(_clean(text)):
        sentence = _HEADING_RE.sub("", raw_sentence).strip()
        length_is_safe = len(sentence) in _CLAIM_LENGTH_RANGE
        if (
            length_is_safe
            and _INSTRUCTION_RE.search(sentence) is None
            and _CLAIM_SIGNAL_RE.search(sentence) is not None
        ):
            return sentence
    return None


def _classify_claim(statement: str) -> ClaimType:
    for claim_type, keywords in _TYPE_KEYWORDS:
        if any(keyword in _normalized(statement) for keyword in keywords):
            return claim_type
    return ClaimType.OTHER


def _near_duplicate(left: PaperClaim, right: PaperClaim) -> bool:
    left_text = _normalized(left.statement)
    right_text = _normalized(right.statement)
    matcher = SequenceMatcher(None, left_text, right_text, autojunk=False)
    return matcher.ratio() >= _SEQUENCE_DUPLICATE_THRESHOLD


def _is_high_level_block(block: PdfBlock) -> bool:
    label = block.section or _clean(block.text)[:80]
    return _HIGH_LEVEL_RE.search(label) is not None


def _stable_claim_id(statement: str) -> str:
    digest = blake2s(_normalized(statement).encode(), digest_size=6).hexdigest()
    return f"C-{digest.upper()}"


def _clean(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    visible = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    return " ".join(visible.split())


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", _clean(value).casefold()))


def _block_locator(block: PdfBlock) -> ClaimLocator:
    return ClaimLocator(
        page=block.page,
        section=block.section,
        locator=block.locator,
        block_id=block.block_id,
    )


def _locator_key(locator: ClaimLocator) -> tuple[int, str, str, str]:
    return (
        locator.page,
        locator.section or "",
        locator.locator or "",
        locator.block_id or "",
    )


def _claim_sort_key(claim: PaperClaim) -> tuple[int, int, str]:
    return (
        _IMPORTANCE_RANKS[claim.importance],
        min(locator.page for locator in claim.reported_evidence),
        _normalized(claim.statement),
    )
