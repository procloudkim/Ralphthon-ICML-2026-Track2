from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2

from reviewharness.api_adapter import (
    AgentCredential,
    ApiAdapterConfig,
    AssignmentBatch,
    RalphthonApiAdapter,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_assignment_batch_resolves_ten_relative_scoped_pdf_urls(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        batch = AssignmentBatch.model_validate(
            {
                "assigned": 10,
                "submitted": 0,
                "remaining": 10,
                "assignments": [
                    {
                        "ordinal": ordinal,
                        "status": None,
                        "paper": {
                            "title": f"Paper {ordinal}",
                            "abstract": "An abstract.",
                            "pdf_url": (
                                "/api/ralphthon/v1/assignments/"
                                f"{ordinal}/pdf"
                            ),
                        },
                    }
                    for ordinal in range(1, 11)
                ],
                "guidance": {
                    "stage": "track2_review",
                    "action_available": True,
                    "reason_code": "assignments_returned",
                    "next_action": "review_assignments",
                    "next_action_actor": "agent",
                    "prerequisites": [
                        {
                            "code": "track2_window_open",
                            "satisfied": True,
                            "actor": "server",
                        }
                    ],
                },
                "future_control_plane_field": "ignored",
            }
        )
        requested_paths: list[str] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.url.scheme == "https"
            assert request.url.host == "openagentreview.org"
            requested_paths.append(request.url.path)
            return httpx2.Response(200, content=b"%PDF-1.7\nfixture")

        credential = AgentCredential.model_validate(
            {
                "access_token": "agent-secret",
                "token_type": "Bearer",
                "guidance": {
                    "stage": "track2_review",
                    "action_available": True,
                    "reason_code": "assignments_returned",
                    "next_action": "review_assignments",
                    "next_action_actor": "agent",
                    "prerequisites": [],
                },
            }
        )
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            adapter = RalphthonApiAdapter(ApiAdapterConfig(), client)
            for assignment in batch.assignments:
                _ = await adapter.download_pdf(
                    credential,
                    assignment,
                    tmp_path / f"{assignment.ordinal}.pdf",
                )

        assert requested_paths == [
            f"/api/ralphthon/v1/assignments/{ordinal}/pdf"
            for ordinal in range(1, 11)
        ]

    anyio.run(scenario)
