from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Final, TypedDict

import anyio
import httpx2
import pytest
from pydantic import HttpUrl, SecretStr, TypeAdapter

from reviewharness.api_adapter import (
    AgentCredential,
    ApiAdapterConfig,
    ApiContractError,
    CredentialExchangeRequest,
    EventAssignment,
    EventReviewPayload,
    IdempotencyKey,
    RalphthonApiAdapter,
    UnsafeDownloadUrlError,
)
from reviewharness.schemas import ReviewSubmission

if TYPE_CHECKING:
    from pathlib import Path


class _GuidancePayload(TypedDict):
    stage: str
    action_available: bool
    reason_code: str
    next_action: str
    next_action_actor: str


type _AsyncHandler = Callable[
    [httpx2.Request],
    Coroutine[None, None, httpx2.Response],
]

_EVENT_URL: Final = HttpUrl("https://event.test")
_CONFIG: Final = ApiAdapterConfig(base_url=_EVENT_URL)
_IDEMPOTENT_CONFIG: Final = ApiAdapterConfig(
    base_url=_EVENT_URL,
    idempotency_header="Idempotency-Key",
)


def _guidance(reason_code: str, next_action: str) -> _GuidancePayload:
    return {
        "stage": "reviewing",
        "action_available": True,
        "reason_code": reason_code,
        "next_action": next_action,
        "next_action_actor": "agent",
    }


def _client(handler: _AsyncHandler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://event.test",
        follow_redirects=True,
        timeout=httpx2.Timeout(5.0),
    )


def _credential() -> AgentCredential:
    return AgentCredential.model_validate(
        {
            "access_token": "agent-secret",
            "token_type": "Bearer",
            "guidance": _guidance("assignments_returned", "get_assignments"),
        },
    )


def _assignment(ordinal: int = 1) -> EventAssignment:
    return EventAssignment.model_validate(
        {
            "ordinal": ordinal,
            "status": "assigned",
            "paper": {
                "title": f"Paper {ordinal}",
                "abstract": "An abstract.",
                "pdf_url": (
                    f"https://event.test/api/ralphthon/v1/assignments/{ordinal}/pdf"
                ),
            },
        },
    )


def _review() -> ReviewSubmission:
    return ReviewSubmission(
        paper_id="trusted-paper-id",
        soundness=3,
        presentation=4,
        significance=2,
        originality=3,
        overall_recommendation=4,
        confidence=4,
        comment="Evidence-grounded comment. " * 6,
    )


def test_exchange_credential_uses_json_body_and_redacts_secret() -> None:
    async def scenario() -> None:
        # Given
        async def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.url.path == "/api/ralphthon/v1/agent-credential/exchange"
            body = TypeAdapter(dict[str, str]).validate_json(await request.aread())
            assert body == {"setup_token": "setup-secret"}
            return httpx2.Response(
                200,
                json={
                    "access_token": "agent-secret",
                    "token_type": "Bearer",
                    "guidance": _guidance("credential_exchanged", "get_assignments"),
                },
            )

        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_CONFIG, client)

            # When
            credential = await adapter.exchange_credential(
                CredentialExchangeRequest(setup_token=SecretStr("setup-secret")),
            )

            # Then
            assert credential.access_token.get_secret_value() == "agent-secret"
            assert "agent-secret" not in repr(credential)

    anyio.run(scenario)


def test_assignments_parse_ordered_ten_and_ignore_outer_additions() -> None:
    async def scenario() -> None:
        # Given
        async def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.headers["authorization"] == "Bearer agent-secret"
            return httpx2.Response(
                200,
                json={
                    "assigned": 10,
                    "submitted": 0,
                    "remaining": 10,
                    "assignments": [
                        {
                            "ordinal": ordinal,
                            "status": "assigned",
                            "paper": {
                                "title": f"Paper {ordinal}",
                                "abstract": "An abstract.",
                                "pdf_url": (
                                    "https://event.test/api/ralphthon/v1/assignments/"
                                    f"{ordinal}/pdf"
                                ),
                            },
                        }
                        for ordinal in range(1, 11)
                    ],
                    "guidance": _guidance(
                        "assignments_returned",
                        "download_and_review_assignments",
                    ),
                    "future_additive_field": "ignored only at this envelope",
                },
            )

        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_CONFIG, client)

            # When
            batch = await adapter.get_assignments(_credential())

            # Then
            assert tuple(item.ordinal for item in batch.assignments) == tuple(
                range(1, 11)
            )

    anyio.run(scenario)


def test_download_pdf_authenticates_and_writes_caller_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Given
        async def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.headers["authorization"] == "Bearer agent-secret"
            return httpx2.Response(200, content=b"%PDF-1.7\nfixture")

        destination = tmp_path / "nested" / "paper.pdf"
        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_CONFIG, client)

            # When
            written = await adapter.download_pdf(
                _credential(),
                _assignment(),
                destination,
            )

            # Then
            assert written == destination
            assert destination.read_bytes() == b"%PDF-1.7\nfixture"

    anyio.run(scenario)


def test_download_pdf_rejects_cross_origin_url(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Given
        unsafe = _assignment().model_copy(
            update={
                "paper": _assignment().paper.model_copy(
                    update={
                        "pdf_url": "https://attacker.test/api/ralphthon/v1/assignments/1/pdf",
                    },
                ),
            },
        )

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(500)

        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_CONFIG, client)

            # When / Then
            with pytest.raises(UnsafeDownloadUrlError):
                _ = await adapter.download_pdf(
                    _credential(),
                    unsafe,
                    tmp_path / "paper.pdf",
                )

    anyio.run(scenario)


def test_submit_translates_exact_payload_and_verifies_receipt() -> None:
    async def scenario() -> None:
        # Given
        async def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.headers["authorization"] == "Bearer agent-secret"
            assert request.headers["idempotency-key"] == "paper-1-config-rubric"
            payload = TypeAdapter(EventReviewPayload).validate_json(
                await request.aread()
            )
            assert payload == {
                "ordinal": 1,
                "soundness": 3,
                "presentation": 4,
                "significance": 2,
                "originality": 3,
                "overall": 4,
                "confidence": 4,
                "comments": "Evidence-grounded comment. " * 6,
            }
            assert "paper_id" not in payload
            return httpx2.Response(
                200,
                json={
                    "review_note_id": "note-1",
                    "forum": "forum-1",
                    "is_first_agent_review": True,
                    "submitted": 10,
                    "remaining": 0,
                    "guidance": _guidance("all_reviews_submitted", "none"),
                },
            )

        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_IDEMPOTENT_CONFIG, client)

            # When
            receipt = await adapter.submit_review(
                _credential(),
                _assignment(),
                _review(),
                IdempotencyKey("paper-1-config-rubric"),
            )

            # Then
            assert receipt.review_note_id == "note-1"
            assert receipt.remaining == 0

    anyio.run(scenario)


def test_submit_rejects_unverified_receipt() -> None:
    async def scenario() -> None:
        # Given
        async def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "forum": "forum-1",
                    "is_first_agent_review": True,
                    "submitted": 1,
                    "remaining": 9,
                    "guidance": _guidance("review_submitted", "submit_review"),
                },
            )

        async with _client(handler) as client:
            adapter = RalphthonApiAdapter(_CONFIG, client)

            # When / Then
            with pytest.raises(ApiContractError):
                _ = await adapter.submit_review(
                    _credential(),
                    _assignment(),
                    _review(),
                )

    anyio.run(scenario)
