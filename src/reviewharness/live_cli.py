"""Expose the fail-closed live event command."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Final, Never

import anyio
import httpx2
import typer
from pydantic import HttpUrl, SecretStr, ValidationError

from .api_adapter import ApiAdapterConfig, ApiContractError
from .live import ensure_live_provider, run_live_event
from .live_support import (
    LiveProvider,
    LiveProviderUnavailableError,
    LiveRunConfig,
    LiveSummary,
)

_DEFAULT_LIVE_OUTPUT: Final = Path("runs/live/current")
_LIVE_SETUP_TOKEN_ENV: Final = "RALPHTHON_SETUP_TOKEN"  # noqa: S105
_LIVE_BASE_URL_ENV: Final = "RALPHTHON_BASE_URL"
_LIVE_IDEMPOTENCY_HEADER_ENV: Final = "RALPHTHON_IDEMPOTENCY_HEADER"


def _fail(code: str) -> Never:
    typer.echo(code, err=True)
    raise typer.Exit(code=1)


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
    paper_concurrency: Annotated[
        int, typer.Option("--paper-concurrency", min=1)
    ] = 5,
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
            idempotency_header=(
                os.environ.get(_LIVE_IDEMPOTENCY_HEADER_ENV) or None
            ),
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
    except (ApiContractError, httpx2.HTTPError, OSError, ValidationError, ValueError):
        _fail("LIVE_RUN_FAILED")
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
