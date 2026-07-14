"""Official live-runbook control-plane adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, override

import httpx2
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictBool,
    StringConstraints,
    ValidationError,
    field_validator,
)

from .api_adapter import (
    AgentCredential,
    ApiAdapterConfig,
    ApiContractError,
    AssignmentBatch,
    CredentialExchangeRequest,
    EventAssignment,
    EventCount,
    IdempotencyKey,
    Prerequisite,
    RalphthonApiAdapter,
    SubmissionReceipt,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .schemas import ReviewSubmission

_API_PREFIX: Final = "/api/ralphthon/v1"
_SKILL_PATH: Final = f"{_API_PREFIX}/skill.md"
_STATUS_PATH: Final = f"{_API_PREFIX}/status"
_OP_CANONICAL_SKILL: Final = "canonical_skill_fetch"
_OP_CREDENTIAL_EXCHANGE: Final = "credential_exchange"
_OP_AUTHENTICATED_STATUS: Final = "authenticated_status"
_OP_ASSIGNMENT_FETCH: Final = "assignment_fetch"
_OP_PDF_DOWNLOAD: Final = "pdf_download"
_OP_REVIEW_SUBMISSION: Final = "review_submission"
type NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class _AdditiveModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class GuidanceReasonCode(StrEnum):
    """Server reason codes that control the next live-run action."""

    ASSIGNMENTS_CAN_BE_CREATED = "assignments_can_be_created"
    ASSIGNMENTS_RETURNED = "assignments_returned"
    REVIEWS_REMAINING = "reviews_remaining"
    ACTIVE_TRACK2_REPORT_REQUIRED = "active_track2_report_required"
    INSUFFICIENT_ELIGIBLE_PAPERS = "insufficient_eligible_papers"
    ALL_REVIEWS_SUBMITTED = "all_reviews_submitted"


class GuidanceTime(_AdditiveModel):
    """Server clock and optional review-window boundaries."""

    timezone: NonEmptyText
    now: AwareDatetime
    window_opens_at: AwareDatetime | None
    window_closes_at: AwareDatetime | None


class StatusGuidance(_AdditiveModel):
    """Additive server guidance used to permit or stop live mutation."""

    stage: NonEmptyText
    action_available: StrictBool
    reason_code: GuidanceReasonCode
    next_action: NonEmptyText
    next_action_actor: NonEmptyText
    time: GuidanceTime
    prerequisites: tuple[Prerequisite, ...]

    @field_validator("prerequisites", mode="before")
    @classmethod
    def normalize_prerequisites(
        cls,
        value: Sequence[object] | None,
    ) -> tuple[Prerequisite, ...]:
        """Normalize legacy string prerequisites into additive typed objects."""
        if value is None:
            return ()
        if not isinstance(value, (tuple, list)):
            raise _UnsupportedPrerequisiteTypeError(value)
        normalized: list[Prerequisite] = []
        for item in value:
            if isinstance(item, Prerequisite):
                normalized.append(item)
            elif isinstance(item, str):
                normalized.append(
                    Prerequisite(
                        code=item,
                        satisfied=True,
                        actor="server",
                    )
                )
            elif isinstance(item, dict):
                normalized.append(Prerequisite.model_validate(item))
            else:
                raise _UnsupportedPrerequisiteTypeError(item)
        return tuple(normalized)


class AuthenticatedStatus(_AdditiveModel):
    """Authenticated event status plus server action guidance."""

    assigned: EventCount | None = None
    submitted: EventCount | None = None
    remaining: EventCount | None = None
    guidance: StatusGuidance


class GuidanceDiagnostic(_AdditiveModel):
    """Redaction-safe subset of guidance fields used in CLI diagnostics."""

    stage: NonEmptyText | None = None
    action_available: StrictBool | None = None
    reason_code: NonEmptyText | None = None
    next_action: NonEmptyText | None = None
    time: GuidanceTime | None = None


class _ErrorEnvelope(_AdditiveModel):
    detail: NonEmptyText | None = None
    guidance: GuidanceDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class RunbookApiError(Exception):
    """Redaction-safe status or transport failure at one runbook operation."""

    operation: str
    status_code: int | None
    detail: str | None
    guidance: GuidanceDiagnostic | None

    @override
    def __str__(self) -> str:
        return self.operation


@dataclass(frozen=True, slots=True)
class StatusValidationIssue:
    """Safe location and type for one malformed status field."""

    location: tuple[str | int, ...]
    error_type: str


class StatusContractError(ApiContractError):
    """Authenticated status failed the additive typed contract."""

    status_issues: tuple[StatusValidationIssue, ...]

    def __init__(self, error: ValidationError) -> None:
        """Retain safe validation metadata without raw server values."""
        self.status_issues = tuple(
            StatusValidationIssue(
                location=tuple(issue["loc"]),
                error_type=issue["type"],
            )
            for issue in error.errors(
                include_context=False,
                include_input=False,
                include_url=False,
            )
        )
        super().__init__("authenticated status", len(self.status_issues))

    @override
    def __str__(self) -> str:
        fields = ",".join(
            f"{'/'.join(str(part) for part in issue.location)}:{issue.error_type}"
            for issue in self.status_issues
        )
        return f"authenticated status validation failed [{fields}]"


def _endpoint(config: ApiAdapterConfig, path: str) -> str:
    return f"{str(config.base_url).rstrip('/')}{path}"


def _authorization(credential: AgentCredential) -> httpx2.Headers:
    token = credential.access_token.get_secret_value()
    return httpx2.Headers({"Authorization": f"{credential.token_type} {token}"})


def _status_error(operation: str, error: httpx2.HTTPStatusError) -> RunbookApiError:
    try:
        envelope = _ErrorEnvelope.model_validate_json(error.response.content)
    except ValidationError:
        envelope = _ErrorEnvelope()
    detail = (
        None if envelope.detail is None else " ".join(envelope.detail.split())[:500]
    )
    return RunbookApiError(
        operation=operation,
        status_code=error.response.status_code,
        detail=detail,
        guidance=envelope.guidance,
    )


def _transport_error(operation: str, error: httpx2.RequestError) -> RunbookApiError:
    return RunbookApiError(operation, None, type(error).__name__, None)


@dataclass(frozen=True, slots=True)
class RunbookApiAdapter:
    """Execute the canonical live runbook around the core event adapter."""

    config: ApiAdapterConfig
    client: httpx2.AsyncClient

    async def fetch_canonical_skill(self) -> None:
        """Fetch the canonical server skill before a mutating operation."""
        try:
            response = await self.client.get(
                _endpoint(self.config, _SKILL_PATH),
                follow_redirects=False,
            )
            _ = response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_CANONICAL_SKILL, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_CANONICAL_SKILL, error) from None

    async def exchange_credential(
        self,
        request: CredentialExchangeRequest,
    ) -> AgentCredential:
        """Exchange the setup token after refreshing canonical guidance."""
        await self.fetch_canonical_skill()
        try:
            return await RalphthonApiAdapter(
                self.config,
                self.client,
            ).exchange_credential(
                request,
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_CREDENTIAL_EXCHANGE, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_CREDENTIAL_EXCHANGE, error) from None

    async def get_status(self, credential: AgentCredential) -> AuthenticatedStatus:
        """Read and validate the authenticated server status."""
        try:
            response = await self.client.get(
                _endpoint(self.config, _STATUS_PATH),
                headers=_authorization(credential),
                follow_redirects=False,
            )
            _ = response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_AUTHENTICATED_STATUS, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_AUTHENTICATED_STATUS, error) from None
        try:
            return AuthenticatedStatus.model_validate_json(response.content)
        except ValidationError as error:
            raise StatusContractError(error) from None

    async def get_assignments(self, credential: AgentCredential) -> AssignmentBatch:
        """Fetch the current assignment batch through the event adapter."""
        try:
            return await RalphthonApiAdapter(self.config, self.client).get_assignments(
                credential
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_ASSIGNMENT_FETCH, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_ASSIGNMENT_FETCH, error) from None

    async def download_pdf(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        destination: Path,
    ) -> Path:
        """Download one assigned PDF through the constrained event adapter."""
        try:
            return await RalphthonApiAdapter(self.config, self.client).download_pdf(
                credential,
                assignment,
                destination,
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_PDF_DOWNLOAD, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_PDF_DOWNLOAD, error) from None

    async def submit_review(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        review: ReviewSubmission,
        idempotency_key: IdempotencyKey | None = None,
    ) -> SubmissionReceipt:
        """Refresh guidance, submit one review, and return a verified receipt."""
        await self.fetch_canonical_skill()
        try:
            return await RalphthonApiAdapter(self.config, self.client).submit_review(
                credential,
                assignment,
                review,
                idempotency_key,
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error(_OP_REVIEW_SUBMISSION, error) from None
        except httpx2.RequestError as error:
            raise _transport_error(_OP_REVIEW_SUBMISSION, error) from None


class _UnsupportedPrerequisiteTypeError(TypeError):
    def __init__(self, value: object) -> None:
        value_type = type(value)
        super().__init__(f"unsupported prerequisite type: {value_type!r}")
