"""Typed Ralphthon event API boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    ClassVar,
    Final,
    Literal,
    NewType,
    TypedDict,
    override,
)

import anyio
import httpx2
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    StrictBool,
    StringConstraints,
    ValidationError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .schemas import ReviewSubmission

_API_PREFIX: Final = "/api/ralphthon/v1"
_ASSIGNMENT_OPERATION: Final = "assignment fetch"
_ASSIGNMENT_COUNT: Final = 10
_PDF_MAGIC: Final = b"%PDF-"
_PDF_OPERATION: Final = "PDF download"
_RECEIPT_OPERATION: Final = "review submission receipt"

type NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]
type AssignmentOrdinal = Annotated[int, Field(strict=True, ge=1, le=10)]
type EventCount = Annotated[int, Field(strict=True, ge=0, le=10)]
IdempotencyKey = NewType("IdempotencyKey", str)


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class _Guidance(_StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    stage: NonEmptyText
    action_available: StrictBool
    reason_code: NonEmptyText
    next_action: NonEmptyText
    next_action_actor: NonEmptyText


class CredentialExchangeRequest(_StrictModel):
    """Single-use human-provided setup credential."""

    setup_token: Annotated[SecretStr, Field(min_length=1)]


class AgentCredential(_StrictModel):
    """Redacted bearer credential returned by a successful exchange."""

    access_token: Annotated[SecretStr, Field(min_length=1)]
    token_type: Literal["Bearer"]
    guidance: _Guidance


class _EventPaper(_StrictModel):
    title: str
    abstract: str
    pdf_url: HttpUrl


class EventAssignment(_StrictModel):
    """Server-owned ordinal and its scoped paper metadata."""

    ordinal: AssignmentOrdinal
    status: NonEmptyText
    paper: _EventPaper


type TenAssignments = Annotated[
    tuple[EventAssignment, ...],
    Field(min_length=10, max_length=10),
]


class AssignmentBatch(_StrictModel):
    """Ordered fixed-ten assignment response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    assigned: Literal[10]
    submitted: EventCount
    remaining: EventCount
    assignments: TenAssignments
    guidance: _Guidance


class SubmissionReceipt(_StrictModel):
    """Verified event receipt for one submitted review."""

    review_note_id: NonEmptyText
    forum: NonEmptyText
    is_first_agent_review: StrictBool
    submitted: EventCount
    remaining: EventCount
    guidance: _Guidance


class EventReviewPayload(TypedDict):
    """Exact organizer-owned review submission wire shape."""

    ordinal: int
    soundness: int
    presentation: int
    significance: int
    originality: int
    overall: int
    confidence: int
    comments: str


class ApiAdapterConfig(_StrictModel):
    """Trusted event endpoint and optional idempotency capability."""

    base_url: HttpUrl = HttpUrl("https://openagentreview.org")
    idempotency_header: NonEmptyText | None = None


class ApiContractError(Exception):
    """An event response failed its typed contract."""

    operation: str
    violation_count: int

    def __init__(self, operation: str, violation_count: int) -> None:
        """Initialize a redaction-safe contract error."""
        self.operation, self.violation_count = operation, violation_count
        super().__init__(operation, violation_count)

    @override
    def __str__(self) -> str:
        return (
            f"{self.operation} response has "
            f"{self.violation_count} contract violation(s)"
        )


class UnsafeDownloadUrlError(Exception):
    """A returned PDF URL escaped its assigned scoped endpoint."""

    ordinal: int

    def __init__(self, ordinal: int) -> None:
        """Initialize without retaining the unsafe URL."""
        self.ordinal = ordinal
        super().__init__(ordinal)

    @override
    def __str__(self) -> str:
        return f"assignment {self.ordinal} returned an unsafe PDF URL"


def _parse_response[ModelT: BaseModel](
    response: httpx2.Response,
    model_type: type[ModelT],
    operation: str,
) -> ModelT:
    try:
        return model_type.model_validate_json(response.content)
    except ValidationError as error:
        raise ApiContractError(operation, error.error_count()) from error


@dataclass(frozen=True, slots=True)
class RalphthonApiAdapter:
    """Call Ralphthon through a caller-owned, fully configured HTTP client."""

    config: ApiAdapterConfig
    client: httpx2.AsyncClient

    async def exchange_credential(
        self,
        request: CredentialExchangeRequest,
    ) -> AgentCredential:
        """Exchange one setup token without placing it in URLs or logs."""
        response = await self.client.post(
            self._endpoint("/agent-credential/exchange"),
            json={"setup_token": request.setup_token.get_secret_value()},
            follow_redirects=False,
        )
        _ = response.raise_for_status()
        return _parse_response(response, AgentCredential, "credential exchange")

    async def get_assignments(self, credential: AgentCredential) -> AssignmentBatch:
        """Fetch the server-owned ordered fixed-ten assignment set."""
        response = await self.client.get(
            self._endpoint("/assignments/current"),
            headers=self._authorization(credential),
            follow_redirects=False,
        )
        _ = response.raise_for_status()
        batch = _parse_response(response, AssignmentBatch, _ASSIGNMENT_OPERATION)
        ordinals = tuple(item.ordinal for item in batch.assignments)
        if ordinals != tuple(range(1, _ASSIGNMENT_COUNT + 1)):
            raise ApiContractError(_ASSIGNMENT_OPERATION, 1)
        return batch

    async def download_pdf(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        destination: Path,
    ) -> Path:
        """Stream one authenticated scoped PDF atomically to the caller path."""
        download_url = httpx2.URL(str(assignment.paper.pdf_url))
        self._verify_download_url(download_url, assignment.ordinal)
        async with self.client.stream(
            "GET",
            download_url,
            headers=self._authorization(credential),
            follow_redirects=False,
        ) as response:
            _ = response.raise_for_status()
            chunks = response.aiter_bytes()
            try:
                first_chunk = await anext(chunks)
            except StopAsyncIteration as error:
                raise ApiContractError(_PDF_OPERATION, 1) from error
            if not first_chunk.startswith(_PDF_MAGIC):
                raise ApiContractError(_PDF_OPERATION, 1)
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(f".{destination.name}.part")
            try:
                async with await anyio.open_file(partial, "wb") as output:
                    _ = await output.write(first_chunk)
                    async for chunk in chunks:
                        _ = await output.write(chunk)
                _ = partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)
        return destination

    async def submit_review(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        review: ReviewSubmission,
        idempotency_key: IdempotencyKey | None = None,
    ) -> SubmissionReceipt:
        """Translate one trusted internal review and verify its event receipt."""
        payload = EventReviewPayload(
            ordinal=assignment.ordinal,
            soundness=review.soundness,
            presentation=review.presentation,
            significance=review.significance,
            originality=review.originality,
            overall=review.overall_recommendation,
            confidence=review.confidence,
            comments=review.comment,
        )
        headers = httpx2.Headers(self._authorization(credential))
        if self.config.idempotency_header is not None and idempotency_key is not None:
            headers[self.config.idempotency_header] = idempotency_key
        response = await self.client.post(
            self._endpoint("/agent-reviews"),
            headers=headers,
            json=payload,
            follow_redirects=False,
        )
        _ = response.raise_for_status()
        receipt = _parse_response(response, SubmissionReceipt, "review submission")
        confirmed = receipt.guidance.reason_code in {
            "review_submitted",
            "all_reviews_submitted",
        }
        if receipt.submitted + receipt.remaining != _ASSIGNMENT_COUNT or not confirmed:
            raise ApiContractError(_RECEIPT_OPERATION, 1)
        return receipt

    def _endpoint(self, path: str) -> str:
        return f"{str(self.config.base_url).rstrip('/')}{_API_PREFIX}{path}"

    def _authorization(self, credential: AgentCredential) -> httpx2.Headers:
        value = f"{credential.token_type} {credential.access_token.get_secret_value()}"
        return httpx2.Headers({"Authorization": value})

    def _verify_download_url(self, url: httpx2.URL, ordinal: int) -> None:
        base_url = httpx2.URL(str(self.config.base_url))
        expected_path = f"{_API_PREFIX}/assignments/{ordinal}/pdf"
        if (
            url.scheme != base_url.scheme
            or url.host != base_url.host
            or url.port != base_url.port
            or url.path != expected_path
            or url.query
        ):
            raise UnsafeDownloadUrlError(ordinal)
