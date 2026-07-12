"""Typed page-aware PDF parsing boundary."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Protocol, TypeGuard, override

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, RootModel

type PdfScalar = str | int | float | bool | None
type PdfValue = PdfScalar | list[PdfValue] | tuple[PdfValue, ...] | dict[str, PdfValue]
type BoundingBox = tuple[float, float, float, float]


class _FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class TextLocator(_FrozenModel):
    """Stable one-indexed page and zero-indexed text coordinates."""

    page: int = Field(ge=1)
    block_index: int = Field(ge=0)
    line_index: int = Field(ge=0)
    span_index: int | None = Field(default=None, ge=0)


class SpanRecord(_FrozenModel):
    """Extracted text span with structural visibility evidence."""

    locator: TextLocator
    bbox: BoundingBox
    text: str
    font_size: float = Field(gt=0)
    opacity: float = Field(ge=0, le=1)
    flags: int


class LineRecord(_FrozenModel):
    """Page line retaining block and line provenance."""

    locator: TextLocator
    bbox: BoundingBox
    text: str
    spans: tuple[SpanRecord, ...]


class PageRecord(_FrozenModel):
    """Page dimensions and ordered extracted lines."""

    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    lines: tuple[LineRecord, ...]
    text: str


class MetadataRecord(_FrozenModel):
    """Inert document metadata inspected as untrusted text."""

    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""


class LinkRecord(_FrozenModel):
    """Unexecuted link indicator."""

    page: int = Field(ge=1)
    kind: int
    uri: str


class AnnotationRecord(_FrozenModel):
    """Unexecuted PDF annotation evidence."""

    page: int = Field(ge=1)
    kind: str
    content: str


class EmbeddedFileRecord(_FrozenModel):
    """Embedded-file indicator and bounded inert text preview."""

    name: str
    filename: str
    description: str
    size: int = Field(ge=0)
    content_preview: str


class ParsedDocument(_FrozenModel):
    """Complete typed output of the untrusted PDF parser."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    pages: tuple[PageRecord, ...]
    metadata: MetadataRecord
    links: tuple[LinkRecord, ...]
    annotations: tuple[AnnotationRecord, ...]
    embedded_files: tuple[EmbeddedFileRecord, ...]


@dataclass(frozen=True, slots=True)
class PdfParseError(Exception):
    """PDF input could not be opened as review evidence."""

    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"cannot parse {self.path}: {self.reason}"


class _RawModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _RawSpan(_RawModel):
    text: str
    size: float
    flags: int = 0
    alpha: int = 255
    bbox: tuple[float, float, float, float]


class _RawLine(_RawModel):
    bbox: tuple[float, float, float, float]
    spans: tuple[_RawSpan, ...] = ()


class _RawBlock(_RawModel):
    type: int
    number: int
    lines: tuple[_RawLine, ...] = ()


class _RawPage(_RawModel):
    width: float
    height: float
    blocks: tuple[_RawBlock, ...]


class _RawLink(_RawModel):
    kind: int
    uri: str = ""


class _RawLinks(RootModel[tuple[_RawLink, ...]]):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class _RawEmbedded(_RawModel):
    name: str
    filename: str = ""
    description: str = ""
    size: int = 0


class _PdfAnnotation(Protocol):
    @property
    def type(self) -> tuple[int, str]: ...

    @property
    def info(self) -> Mapping[str, str | None]: ...


class _PdfPage(Protocol):
    def get_text(self, option: str, *, sort: bool = False) -> PdfValue: ...

    def get_links(self) -> PdfValue: ...

    def annots(self) -> Iterator[_PdfAnnotation] | None: ...


class _PdfDocument(Protocol):
    @property
    def metadata(self) -> Mapping[str, str | None] | None: ...

    @property
    def page_count(self) -> int: ...

    @property
    def needs_pass(self) -> int: ...

    def load_page(self, page_id: int) -> _PdfPage: ...

    def embfile_count(self) -> int: ...

    def embfile_info(self, item: int) -> PdfValue: ...

    def embfile_get(self, item: int) -> bytes: ...


def _supports_pdf_contract(document: pymupdf.Document) -> TypeGuard[_PdfDocument]:
    return all(
        hasattr(document, name)
        for name in ("load_page", "embfile_count", "embfile_info", "embfile_get")
    )


def _parse_page(
    page: _PdfPage, page_number: int
) -> tuple[PageRecord, tuple[LinkRecord, ...], tuple[AnnotationRecord, ...]]:
    raw = _RawPage.model_validate(page.get_text("dict", sort=True))
    lines: list[LineRecord] = []
    for block in raw.blocks:
        if block.type != 0:
            continue
        for line_index, line in enumerate(block.lines):
            spans = tuple(
                SpanRecord(
                    locator=TextLocator(
                        page=page_number,
                        block_index=block.number,
                        line_index=line_index,
                        span_index=span_index,
                    ),
                    bbox=span.bbox,
                    text=span.text,
                    font_size=span.size,
                    opacity=span.alpha / 255,
                    flags=span.flags,
                )
                for span_index, span in enumerate(line.spans)
            )
            lines.append(
                LineRecord(
                    locator=TextLocator(
                        page=page_number,
                        block_index=block.number,
                        line_index=line_index,
                    ),
                    bbox=line.bbox,
                    text="".join(span.text for span in spans),
                    spans=spans,
                )
            )
    links = tuple(
        LinkRecord(page=page_number, kind=link.kind, uri=link.uri)
        for link in _RawLinks.model_validate(page.get_links()).root
    )
    page_annotations = page.annots()
    annotations = (
        tuple(
            AnnotationRecord(
                page=page_number,
                kind=annotation.type[1],
                content=annotation.info.get("content") or "",
            )
            for annotation in page_annotations
        )
        if page_annotations is not None
        else ()
    )
    return (
        PageRecord(
            number=page_number,
            width=raw.width,
            height=raw.height,
            lines=tuple(lines),
            text="\n".join(line.text for line in lines),
        ),
        links,
        annotations,
    )


def _parse_document(document: _PdfDocument, digest: str) -> ParsedDocument:
    if document.needs_pass:
        raise PdfParseError(path=Path("<encrypted>"), reason="password required")
    parsed_pages = tuple(
        _parse_page(document.load_page(index), index + 1)
        for index in range(document.page_count)
    )
    metadata = document.metadata or {}
    embedded = tuple(
        EmbeddedFileRecord(
            name=raw.name,
            filename=raw.filename,
            description=raw.description,
            size=raw.size,
            content_preview=document.embfile_get(index)[:4096].decode(
                "utf-8", errors="replace"
            ),
        )
        for index in range(document.embfile_count())
        for raw in (_RawEmbedded.model_validate(document.embfile_info(index)),)
    )
    return ParsedDocument(
        sha256=digest,
        page_count=document.page_count,
        pages=tuple(item[0] for item in parsed_pages),
        metadata=MetadataRecord(
            title=metadata.get("title") or "",
            author=metadata.get("author") or "",
            subject=metadata.get("subject") or "",
            keywords=metadata.get("keywords") or "",
            creator=metadata.get("creator") or "",
            producer=metadata.get("producer") or "",
        ),
        links=tuple(link for item in parsed_pages for link in item[1]),
        annotations=tuple(
            annotation for item in parsed_pages for annotation in item[2]
        ),
        embedded_files=embedded,
    )


def parse_pdf(path: Path) -> ParsedDocument:
    """Parse a local PDF without executing document actions or links."""
    try:
        digest = sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as opened:
            if not _supports_pdf_contract(opened):
                raise PdfParseError(path=path, reason="unsupported PyMuPDF contract")
            return _parse_document(opened, digest)
    except (OSError, pymupdf.EmptyFileError, pymupdf.FileDataError) as error:
        raise PdfParseError(path=path, reason=str(error)) from error
