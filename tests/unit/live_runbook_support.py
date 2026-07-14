from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, final

import httpx2
import pytest
from pydantic import HttpUrl, TypeAdapter

from reviewharness.api_adapter import (
    AgentCredential,
    ApiAdapterConfig,
    EventReviewPayload,
)
from reviewharness.live_support import LiveProvider, LiveRunConfig
from reviewharness.schemas import ReviewSubmission

if TYPE_CHECKING:
    from pathlib import Path

EVENT_URL: Final = HttpUrl("https://event.test")
CONFIG: Final = ApiAdapterConfig(base_url=EVENT_URL)
SKILL_PATH: Final = "/api/ralphthon/v1/skill.md"
EXCHANGE_PATH: Final = "/api/ralphthon/v1/agent-credential/exchange"
STATUS_PATH: Final = "/api/ralphthon/v1/status"
ASSIGNMENTS_PATH: Final = "/api/ralphthon/v1/assignments/current"
REVIEWS_PATH: Final = "/api/ralphthon/v1/agent-reviews"


def guidance(
    reason_code: str,
    next_action: str,
    *,
    next_action_actor: str = "agent",
) -> dict[str, str | bool]:
    return {
        "stage": "track2_review",
        "action_available": True,
        "reason_code": reason_code,
        "next_action": next_action,
        "next_action_actor": next_action_actor,
    }


def status_guidance(
    reason_code: str,
    *,
    action_available: bool = True,
    next_action: str = "get_assignments",
    next_action_actor: str = "agent",
) -> dict[str, str | bool | list[str] | dict[str, str]]:
    return {
        "stage": "track2_review",
        "action_available": action_available,
        "reason_code": reason_code,
        "next_action": next_action,
        "next_action_actor": next_action_actor,
        "time": {
            "timezone": "Asia/Seoul",
            "now": "2026-07-12T16:35:00+09:00",
            "window_opens_at": "2026-07-12T16:35:00+09:00",
            "window_closes_at": "2026-07-12T16:45:00+09:00",
        },
        "prerequisites": ["setup_token_exchanged"],
    }


def assignment(ordinal: int) -> dict[str, int | str | dict[str, str]]:
    return {
        "ordinal": ordinal,
        "status": "assigned",
        "paper": {
            "title": f"Paper {ordinal}",
            "abstract": "An abstract.",
            "pdf_url": (
                f"https://event.test/api/ralphthon/v1/assignments/{ordinal}/pdf"
            ),
        },
    }


@final
@dataclass(slots=True)
class RunbookHandler:
    reason_code: str = "assignments_can_be_created"
    action_available: bool = True
    next_action: str = "get_assignments"
    next_action_actor: str = "agent"
    requests: list[tuple[str, str]] = field(default_factory=list)
    review_payload: EventReviewPayload | None = None

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.path))
        match request.url.path:
            case path if path == SKILL_PATH:
                return httpx2.Response(200, text="# canonical skill")
            case path if path == EXCHANGE_PATH:
                return httpx2.Response(
                    200,
                    json={
                        "access_token": "bearer-secret",
                        "token_type": "Bearer",
                        "guidance": guidance(
                            "credential_exchanged",
                            "get_status",
                            next_action_actor=self.next_action_actor,
                        ),
                    },
                )
            case path if path == STATUS_PATH:
                return httpx2.Response(
                    200,
                    json={
                        "assigned": 0,
                        "submitted": 0,
                        "remaining": 0,
                        "guidance": status_guidance(
                            self.reason_code,
                            action_available=self.action_available,
                            next_action=self.next_action,
                            next_action_actor=self.next_action_actor,
                        ),
                        "future_status_field": "allowed",
                    },
                )
            case path if path == ASSIGNMENTS_PATH:
                return httpx2.Response(
                    200,
                    json={
                        "assigned": 10,
                        "submitted": 0,
                        "remaining": 10,
                        "assignments": [assignment(index) for index in range(1, 11)],
                        "guidance": guidance(
                            "assignments_returned",
                            "review_assignments",
                            next_action_actor=self.next_action_actor,
                        ),
                    },
                )
            case path if path == REVIEWS_PATH:
                self.review_payload = TypeAdapter(EventReviewPayload).validate_json(
                    await request.aread()
                )
                return httpx2.Response(
                    200,
                    json={
                        "review_note_id": "note-1",
                        "forum": "forum-1",
                        "is_first_agent_review": True,
                        "submitted": 1,
                        "remaining": 9,
                        "guidance": guidance(
                            "review_submitted",
                            "submit_review",
                            next_action_actor=self.next_action_actor,
                        ),
                    },
                )
            case unexpected:
                pytest.fail(f"unexpected request path: {unexpected}")


def client(handler: RunbookHandler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url=str(EVENT_URL),
        follow_redirects=False,
    )


def config(output_dir: Path) -> LiveRunConfig:
    return LiveRunConfig(
        provider=LiveProvider.CODEX_EXEC,
        paper_concurrency=5,
        deadline_seconds=1_500.0,
        output_dir=output_dir,
    )


def credential() -> AgentCredential:
    return AgentCredential.model_validate(
        {
            "access_token": "bearer-secret",
            "token_type": "Bearer",
            "guidance": guidance("assignments_returned", "review_assignments"),
        }
    )


def review() -> ReviewSubmission:
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
