"""Official live-runbook control-plane adapter."""

from __future__ import annotations

from collections.abc import Sequence
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
    from pathlib import Path

    from .schemas import ReviewSubmission

_API_PREFIX: Final = "/api/ralphthon/v1"
_SKILL_PATH: Final = f"{_API_PREFIX}/skill.md"
_STATUS_PATH: Final = f"{_API_PREFIX}/status"
type NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class _AdditiveModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class GuidanceReasonCode(StrEnum):
    ASSIGNMENTS_CAN_BE_CREATED = "assignments_can_be_created"
    ASSIGNMENTS_RETURNED = "assignments_returned"
    REVIEWS_REMAINING = "reviews_remaining"
    ACTIVE_TRACK2_REPORT_REQUIRED = "active_track2_report_required"
    INSUFFICIENT_ELIGIBLE_PAPERS = "insufficient_eligible_papers"
    ALL_REVIEWS_SUBMITTED = "all_reviews_submitted"


class GuidanceTime(_AdditiveModel):
    timezone: NonEmptyText
    now: AwareDatetime
    window_opens_at: AwareDatetime | None
    window_closes_at: AwareDatetime | None


class StatusGuidance(_AdditiveModel):
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
        if value is None:
            return ()
        if not isinstance(value, (tuple, list)):
            raise TypeError(f"unsupported prerequisite type: {type(value)!r}")
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
                raise TypeError(f"unsupported prerequisite type: {type(item)!r}")
        return tuple(normalized)


class AuthenticatedStatus(_AdditiveModel):
    assigned: EventCount | None = None
    submitted: EventCount | None = None
    remaining: EventCount | None = None
    guidance: StatusGuidance


class GuidanceDiagnostic(_AdditiveModel):
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
    operation: str
    status_code: int | None
    detail: str | None
    guidance: GuidanceDiagnostic | None

    @override
    def __str__(self) -> str:
        return self.operation


@dataclass(frozen=True, slots=True)
class StatusValidationIssue:
    location: tuple[str | int, ...]
    error_type: str


class StatusContractError(ApiContractError):
    status_issues: tuple[StatusValidationIssue, ...]

    def __init__(self, error: ValidationError) -> None:
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
    detail = None if envelope.detail is None else " ".join(envelope.detail.split())[:500]
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
    config: ApiAdapterConfig
    client: httpx2.AsyncClient

    async def fetch_canonical_skill(self) -> None:
        try:
            response = await self.client.get(
                _endpoint(self.config, _SKILL_PATH),
                follow_redirects=False,
            )
            _ = response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            raise _status_error("canonical_skill_fetch", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("canonical_skill_fetch", error) from None

    async def exchange_credential(
        self,
        request: CredentialExchangeRequest,
    ) -> AgentCredential:
        await self.fetch_canonical_skill()
        try:
            return await RalphthonApiAdapter(self.config, self.client).exchange_credential(
                request
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error("credential_exchange", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("credential_exchange", error) from None

    async def get_status(self, credential: AgentCredential) -> AuthenticatedStatus:
        try:
            response = await self.client.get(
                _endpoint(self.config, _STATUS_PATH),
                headers=_authorization(credential),
                follow_redirects=False,
            )
            _ = response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            raise _status_error("authenticated_status", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("authenticated_status", error) from None
        try:
            return AuthenticatedStatus.model_validate_json(response.content)
        except ValidationError as error:
            raise StatusContractError(error) from None

    async def get_assignments(self, credential: AgentCredential) -> AssignmentBatch:
        try:
            return await RalphthonApiAdapter(self.config, self.client).get_assignments(
                credential
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error("assignment_fetch", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("assignment_fetch", error) from None

    async def download_pdf(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        destination: Path,
    ) -> Path:
        try:
            return await RalphthonApiAdapter(self.config, self.client).download_pdf(
                credential,
                assignment,
                destination,
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error("pdf_download", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("pdf_download", error) from None

    async def submit_review(
        self,
        credential: AgentCredential,
        assignment: EventAssignment,
        review: ReviewSubmission,
        idempotency_key: IdempotencyKey | None = None,
    ) -> SubmissionReceipt:
        await self.fetch_canonical_skill()
        try:
            return await RalphthonApiAdapter(self.config, self.client).submit_review(
                credential,
                assignment,
                review,
                idempotency_key,
            )
        except httpx2.HTTPStatusError as error:
            raise _status_error("review_submission", error) from None
        except httpx2.RequestError as error:
            raise _transport_error("review_submission", error) from None
