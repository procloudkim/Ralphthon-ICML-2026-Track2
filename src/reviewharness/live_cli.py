"""Expose the fail-closed live event command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Final, Never

import anyio
import httpx2
import typer
from pydantic import HttpUrl, SecretStr, ValidationError

from .api_adapter import ApiAdapterConfig, ApiContractError
from .live import LiveGuidanceStopError, ensure_live_provider, run_live_event
from .live_support import (
    LiveProvider,
    LiveProviderUnavailableError,
    LiveRunConfig,
    LiveSummary,
)
from .runbook_adapter import GuidanceDiagnostic, RunbookApiError

_DEFAULT_LIVE_OUTPUT: Final = Path("runs/live/current")
_LIVE_SETUP_TOKEN_ENV: Final = "RALPHTHON_SETUP_TOKEN"  # noqa: S105
_LIVE_BASE_URL_ENV: Final = "RALPHTHON_BASE_URL"
_LIVE_IDEMPOTENCY_HEADER_ENV: Final = "RALPHTHON_IDEMPOTENCY_HEADER"


def _fail(code: str) -> Never:
    typer.echo(code, err=True)
    raise typer.Exit(code=1)


def _guidance_fields(guidance: GuidanceDiagnostic) -> tuple[str, ...]:
    time = guidance.time
    now = None if time is None else time.now
    window_opens_at = None if time is None else time.window_opens_at
    window_closes_at = None if time is None else time.window_closes_at
    now_text = "unknown" if now is None else now.isoformat()
    opens_text = "unknown" if window_opens_at is None else window_opens_at.isoformat()
    closes_text = (
        "unknown" if window_closes_at is None else window_closes_at.isoformat()
    )
    return (
        f"stage={guidance.stage or 'unknown'}",
        f"reason_code={guidance.reason_code or 'unknown'}",
        f"next_action={guidance.next_action or 'unknown'}",
        f"action_available={guidance.action_available}",
        f"server_now={now_text}",
        f"window_opens_at={opens_text}",
        f"window_closes_at={closes_text}",
    )


def _format_api_error(error: RunbookApiError) -> str:
    guidance = error.guidance or GuidanceDiagnostic()
    return " ".join(
        (
            "LIVE_RUN_FAILED",
            f"operation={error.operation}",
            f"http_status={error.status_code or 'unknown'}",
            f"api_detail={json.dumps(error.detail or 'unavailable')}",
            *_guidance_fields(guidance),
        )
    )


def _format_guidance_stop(error: LiveGuidanceStopError) -> str:
    guidance = GuidanceDiagnostic.model_validate(error.guidance.model_dump())
    code = "LIVE_ALREADY_COMPLETE" if error.success else "LIVE_GUIDANCE_STOP"
    return " ".join((code, "operation=status_guidance", *_guidance_fields(guidance)))


async def _run_live(
    setup_token: SecretStr,
    api_config: ApiAdapterConfig,
    run_config: LiveRunConfig,
) -> LiveSummary:
    return await run_live_event(setup_token, api_config, run_config)


def live_command(
    provider: Annotated[LiveProvider, typer.Option("--provider")] = (
        LiveProvider.CODEX_EXEC
    ),
    paper_concurrency: Annotated[int, typer.Option("--paper-concurrency", min=1)] = 5,
    deadline_seconds: Annotated[
        float, typer.Option("--deadline-seconds", min=1.0)
    ] = 1_500.0,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = _DEFAULT_LIVE_OUTPUT,
) -> None:
    """Fetch, review, validate, submit, and receipt-check ten live papers."""
    try:
        ensure_live_provider(provider)
    except LiveProviderUnavailableError:
        _fail("LIVE_PROVIDER_UNAVAILABLE")
    raw_setup_token = os.environ.get(_LIVE_SETUP_TOKEN_ENV)
    if raw_setup_token is None or not raw_setup_token.strip():
        _fail("LIVE_SETUP_TOKEN_MISSING")
    try:
        api_config = ApiAdapterConfig(
            base_url=HttpUrl(
                os.environ.get(
                    _LIVE_BASE_URL_ENV,
                    "https://openagentreview.org",
                )
            ),
            idempotency_header=(os.environ.get(_LIVE_IDEMPOTENCY_HEADER_ENV) or None),
        )
        run_config = LiveRunConfig(
            provider=provider,
            paper_concurrency=paper_concurrency,
            deadline_seconds=deadline_seconds,
            output_dir=output_dir,
        )
        summary = anyio.run(
            _run_live,
            SecretStr(raw_setup_token),
            api_config,
            run_config,
        )
    except LiveGuidanceStopError as error:
        typer.echo(_format_guidance_stop(error), err=not error.success)
        if error.success:
            return
        raise typer.Exit(code=1) from None
    except RunbookApiError as error:
        _fail(_format_api_error(error))
    except ApiContractError as error:
        _fail(
            " ".join(
                (
                    "LIVE_RUN_FAILED",
                    f"operation={error.operation}",
                    "http_status=unknown",
                    f"api_detail={json.dumps(str(error))}",
                )
            )
        )
    except (httpx2.HTTPError, OSError, ValidationError, ValueError) as error:
        _fail(
            " ".join(
                (
                    "LIVE_RUN_FAILED",
                    "operation=live_run",
                    "http_status=unknown",
                    f"api_detail={json.dumps(type(error).__name__)}",
                )
            )
        )
    for item in summary.items:
        typer.echo(
            " ".join(
                (
                    "live_paper_complete",
                    f"paper_id={item.paper_id}",
                    f"mode={item.review_mode.value}",
                    f"status={item.status.value}",
                    f"receipt={item.receipt_verified}",
                )
            )
        )
    typer.echo(
        " ".join(
            (
                "live_complete",
                f"submitted={len(summary.items) - summary.failed_count}",
                f"requested={len(summary.items)}",
            )
        )
    )
    if summary.failed_count:
        raise typer.Exit(code=1)
