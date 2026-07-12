from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import reviewharness.live_cli as live_cli
from reviewharness.api_adapter import ApiAdapterConfig, ApiContractError, EventAssignment
from reviewharness.cli import app
from reviewharness.live import LiveGuidanceStop, prepare_live_run
from reviewharness.live_support import LiveRunConfig, LiveSummary
from reviewharness.runbook_adapter import (
    GuidanceDiagnostic,
    RunbookApiAdapter,
    RunbookApiError,
)
from .live_runbook_support import (
    ASSIGNMENTS_PATH,
    CONFIG,
    EXCHANGE_PATH,
    REVIEWS_PATH,
    SKILL_PATH,
    STATUS_PATH,
    RunbookHandler,
    assignment,
    client,
    config,
    credential,
    review,
    status_guidance,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_prepare_orders_skill_exchange_status_assignment_and_server_deadline(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler()
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)

            # When
            prepared = await prepare_live_run(
                adapter,
                SecretStr("setup-secret"),
                config(tmp_path),
            )

        # Then
        assert handler.requests == [
            ("GET", SKILL_PATH),
            ("GET", SKILL_PATH),
            ("POST", EXCHANGE_PATH),
            ("GET", STATUS_PATH),
            ("GET", ASSIGNMENTS_PATH),
        ]
        assert len(prepared.assignments) == 10
        assert prepared.deadline_seconds == 480.0

    anyio.run(scenario)


def test_review_post_refetches_skill_and_preserves_exact_payload() -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler()
        event_assignment = EventAssignment.model_validate(assignment(1))
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)

            # When
            _ = await adapter.submit_review(
                credential(),
                event_assignment,
                review(),
            )

        # Then
        assert handler.requests == [("GET", SKILL_PATH), ("POST", REVIEWS_PATH)]
        assert handler.review_payload == {
            "ordinal": 1,
            "soundness": 3,
            "presentation": 4,
            "significance": 2,
            "originality": 3,
            "overall": 4,
            "confidence": 4,
            "comments": "Evidence-grounded comment. " * 6,
        }

    anyio.run(scenario)


def test_no_action_guidance_prevents_assignment_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler(
            reason_code="assignments_returned",
            action_available=False,
            next_action="none",
        )
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)

            # When / Then
            with pytest.raises(LiveGuidanceStop):
                _ = await prepare_live_run(
                    adapter,
                    SecretStr("setup-secret"),
                    config(tmp_path),
                )
        assert ("GET", ASSIGNMENTS_PATH) not in handler.requests
        assert ("POST", REVIEWS_PATH) not in handler.requests

    anyio.run(scenario)


@pytest.mark.parametrize(
    "reason_code",
    ["active_track2_report_required", "insufficient_eligible_papers"],
)
def test_blocking_guidance_stops_before_assignment_allocation(
    reason_code: str,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler(reason_code=reason_code)
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)

            # When
            with pytest.raises(LiveGuidanceStop) as captured:
                _ = await prepare_live_run(
                    adapter,
                    SecretStr("setup-secret"),
                    config(tmp_path),
                )

        # Then
        assert captured.value.success is False
        assert captured.value.guidance.reason_code.value == reason_code
        assert ("GET", ASSIGNMENTS_PATH) not in handler.requests

    anyio.run(scenario)


def test_all_reviews_submitted_stops_successfully(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler(
            reason_code="all_reviews_submitted",
            action_available=False,
            next_action="none",
        )
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)

            # When
            with pytest.raises(LiveGuidanceStop) as captured:
                _ = await prepare_live_run(
                    adapter,
                    SecretStr("setup-secret"),
                    config(tmp_path),
                )

        # Then
        assert captured.value.success is True
        assert ("GET", ASSIGNMENTS_PATH) not in handler.requests

    anyio.run(scenario)


def test_invalid_action_actor_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Given
        handler = RunbookHandler(next_action_actor="malicious_actor")
        async with client(handler) as http_client:
            adapter = RunbookApiAdapter(CONFIG, http_client)
            with pytest.raises(ApiContractError):
                _ = await prepare_live_run(
                    adapter,
                    SecretStr("setup-secret"),
                    config(tmp_path),
                )
        assert ("GET", ASSIGNMENTS_PATH) not in handler.requests

    anyio.run(scenario)


def test_live_cli_prints_only_redaction_safe_error_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_run(
        _token: SecretStr,
        _api_config: ApiAdapterConfig,
        _run_config: LiveRunConfig,
    ) -> LiveSummary:
        raise RunbookApiError(
            operation="authenticated_status",
            status_code=409,
            detail="window unavailable",
            guidance=GuidanceDiagnostic.model_validate(
                status_guidance("insufficient_eligible_papers")
            ),
        )

    # Given
    monkeypatch.setattr(live_cli, "_run_live", failing_run)

    # When
    result = CliRunner().invoke(
        app,
        ["live"],
        env={"RALPHTHON_SETUP_TOKEN": "setup-secret"},
    )

    # Then
    assert result.exit_code == 1
    assert "operation=authenticated_status" in result.output
    assert "http_status=409" in result.output
    assert "api_detail=\"window unavailable\"" in result.output
    assert "reason_code=insufficient_eligible_papers" in result.output
    assert "server_now=2026-07-12T16:35:00+09:00" in result.output
    for forbidden in (
        "setup-secret",
        "bearer-secret",
        "Authorization",
        "credential_hash",
        "owner_identity",
        "canonical_paper_identity",
        "storage_key",
    ):
        assert forbidden not in result.output
