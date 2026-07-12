"""Stream live Ralphthon assignments through the isolated Codex reviewer."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Final

import anyio

from .api_adapter import (
    ApiAdapterConfig,
    CredentialExchangeRequest,
    EventAssignment,
    RalphthonApiAdapter,
)
from .artifacts import ArtifactPathError, ArtifactStore
from .codex_provider import CodexExecReviewerProvider
from .deadline import ReviewMode
from .kernel import KernelReviewError, ReviewKernel, ReviewKernelPolicy
from .live_support import (
    LivePaperResult,
    LiveProvider,
    LiveProviderUnavailableError,
    LiveResultContext,
    LiveReviewMode,
    LiveRunConfig,
    LiveSummary,
    completion_payload,
    create_event_client,
    failure_result,
    idempotency_key,
    success_result,
)
from .local_provider import LocalHeuristicProvider
from .schemas import TrustedAssignment

if TYPE_CHECKING:
    from pydantic import SecretStr

_MODEL_TIMEOUT_SECONDS: Final = 240.0
_PER_PAPER_TIMEOUT_SECONDS: Final = 300.0
_RESERVE_SECONDS: Final = 120.0
def ensure_live_provider(provider: LiveProvider) -> None:
    """Fail before credentials or assignments when Codex is not selected."""
    if provider is LiveProvider.LOCAL_HEURISTIC:
        raise LiveProviderUnavailableError


async def run_live_event(
    setup_token: SecretStr,
    api_config: ApiAdapterConfig,
    run_config: LiveRunConfig,
) -> LiveSummary:
    """Fetch ten assignments and submit each validated review immediately."""
    ensure_live_provider(run_config.provider)
    started_at = monotonic()
    store = ArtifactStore(run_config.output_dir)
    results: dict[int, LivePaperResult] = {}
    paper_limiter = anyio.CapacityLimiter(run_config.paper_concurrency)
    model_limiter = anyio.CapacityLimiter(run_config.paper_concurrency)
    codex_kernel = ReviewKernel(
        CodexExecReviewerProvider(),
        policy=ReviewKernelPolicy(
            timeout_seconds=_MODEL_TIMEOUT_SECONDS,
            require_reviewer_output=True,
            retry_reviewer_failures=False,
        ),
    )
    fallback_kernel = ReviewKernel(
        LocalHeuristicProvider(),
        policy=ReviewKernelPolicy(confidence_cap=2),
    )
    async with create_event_client() as client:
        adapter = RalphthonApiAdapter(api_config, client)
        credential = await adapter.exchange_credential(
            CredentialExchangeRequest(setup_token=setup_token)
        )
        batch = await adapter.get_assignments(credential)

        async def worker(assignment: EventAssignment) -> None:
            async with paper_limiter:
                paper_started = monotonic()
                paper_id = f"ordinal-{assignment.ordinal:02d}"
                review_mode = LiveReviewMode.CODEX_EXEC
                retries = 0
                try:
                    usable = _usable_seconds(
                        run_config,
                        started_at,
                        paper_started,
                    )
                    with anyio.fail_after(usable):
                        pdf_path = await adapter.download_pdf(
                            credential,
                            assignment,
                            store.paper_directory(paper_id) / "paper.pdf",
                        )
                        trusted = TrustedAssignment(
                            paper_id=paper_id,
                            pdf_path=pdf_path,
                            title=assignment.paper.title or None,
                            ordinal=assignment.ordinal,
                        )
                        try:
                            review = await codex_kernel.review(
                                trusted,
                                ReviewMode.FAST,
                                store.root,
                                model_limiter,
                            )
                        except KernelReviewError as error:
                            if error.failure_kind != "reviewer_provider_unavailable":
                                raise
                            review_mode = LiveReviewMode.HEURISTIC_FALLBACK
                            retries = 1
                            review = await fallback_kernel.review(
                                trusted,
                                ReviewMode.FAST,
                                store.root,
                                model_limiter,
                            )
                        receipt = await adapter.submit_review(
                            credential,
                            assignment,
                            review,
                            idempotency_key(assignment, review),
                        )
                        _ = store.write_json(
                            paper_id,
                            "submission_receipt",
                            receipt.model_dump(mode="json"),
                        )
                    result = success_result(
                        LiveResultContext(
                            paper_id,
                            assignment.ordinal,
                            review_mode,
                            paper_started,
                            retries,
                        ),
                        review,
                    )
                except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                    result = failure_result(
                        LiveResultContext(
                            paper_id,
                            assignment.ordinal,
                            review_mode,
                            paper_started,
                            retries,
                        ),
                        type(error).__name__,
                    )
                results[assignment.ordinal] = result
                try:
                    _ = store.append_completion(paper_id, completion_payload(result))
                except (ArtifactPathError, OSError, ValueError):
                    results[assignment.ordinal] = failure_result(
                        LiveResultContext(
                            paper_id,
                            assignment.ordinal,
                            review_mode,
                            paper_started,
                            retries,
                        ),
                        "completion_record_failed",
                    )

        async with anyio.create_task_group() as task_group:
            for assignment in batch.assignments:
                _ = task_group.start_soon(worker, assignment)
    items = tuple(results[ordinal] for ordinal in sorted(results))
    return LiveSummary(items=items, total_seconds=monotonic() - started_at)


def _usable_seconds(
    config: LiveRunConfig,
    run_started: float,
    paper_started: float,
) -> float:
    remaining = config.deadline_seconds - (paper_started - run_started)
    usable = min(_PER_PAPER_TIMEOUT_SECONDS, remaining - _RESERVE_SECONDS)
    if usable <= 0.0:
        raise TimeoutError
    return usable
