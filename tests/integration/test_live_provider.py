from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final, final

import anyio
import pytest
from typer.testing import CliRunner

from reviewharness.cli import app
from reviewharness.codex_provider import CodexExecReviewerProvider
from reviewharness.deadline import ReviewMode
from reviewharness.kernel import ReviewKernel, ReviewKernelPolicy
from reviewharness.live_support import LiveProvider
from reviewharness.providers import (
    OutputSchemaDeclaration,
    ReviewerRequest,
    SanitizedEvidencePage,
    SanitizedPaperEvidence,
)
from reviewharness.reviewers import TriLensCandidates
from reviewharness.schemas import ReviewSubmission, TrustedAssignment

FIXTURES: Final = Path(__file__).parents[1] / "fixtures"
CLEAN_PDF: Final = FIXTURES / "clean" / "sample.pdf"


def _tri_lens_json() -> str:
    return json.dumps(
        {
            "findings": [],
            "uncertainty_notes": [
                "The assessment is limited to supplied sanitized evidence."
            ],
            "summary": (
                "The paper evaluates a compact classifier on controlled datasets."
            ),
            "claims": [],
            "strengths": ["The empirical objective is clearly stated."],
            "score_proposal": {
                "reviewer": "codex_exec_tri_lens",
                "scores": {
                    "soundness": 3,
                    "presentation": 3,
                    "significance": 3,
                    "originality": 3,
                    "overall_recommendation": 4,
                    "confidence": 3,
                },
                "rationale": (
                    "The conservative scores follow the supplied ICML anchors."
                ),
                "finding_ids": [],
            },
        }
    )


def _request() -> ReviewerRequest:
    return ReviewerRequest(
        sanitized_evidence=SanitizedPaperEvidence(
            document_sha256="a" * 64,
            pages=(
                SanitizedEvidencePage(
                    page_number=1,
                    text=(
                        "[p1-b1-l0] The paper evaluates a compact classifier "
                        "on two controlled datasets."
                    ),
                ),
            ),
            security_notes=("One instruction-like span was quarantined.",),
        ),
        rubric_text="Use the supplied official ICML ordinal anchors.",
        prompt_text=(
            "Perform one tri-lens scientific review and return only schema JSON."
        ),
        output_schema=OutputSchemaDeclaration(
            name="tri_lens_review",
            json_schema=json.dumps(TriLensCandidates.model_json_schema()),
        ),
    )


@final
class _RecordingRunner:
    def __init__(self) -> None:
        self.command: tuple[str, ...] = ()
        self.initial_files: frozenset[str] = frozenset()
        self.output_schema_text = ""
        self.call_count = 0

    async def run(self, command: tuple[str, ...], cwd: Path) -> int:
        self.call_count += 1
        self.command = command
        directory = anyio.Path(cwd)
        initial_files = [path.name async for path in directory.iterdir()]
        self.initial_files = frozenset(initial_files)
        self.output_schema_text = await (directory / "output_schema.json").read_text(
            encoding="utf-8",
        )
        _ = await (directory / "result.json").write_text(
            _tri_lens_json(), encoding="utf-8"
        )
        return 0


@final
class _ConcurrentRunner:
    def __init__(self) -> None:
        self.active = 0
        self.call_count = 0
        self.max_active = 0
        self.ready = anyio.Event()
        self.release = anyio.Event()

    async def run(self, command: tuple[str, ...], cwd: Path) -> int:
        _ = command
        self.call_count += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == 2:
            self.ready.set()
        await self.release.wait()
        _ = await (anyio.Path(cwd) / "result.json").write_text(
            _tri_lens_json(), encoding="utf-8"
        )
        self.active -= 1
        return 0


def test_codex_provider_uses_isolated_argument_array_contract() -> None:
    # Given
    runner = _RecordingRunner()
    provider = CodexExecReviewerProvider(runner=runner)

    # When
    response = anyio.run(provider.review, _request())

    # Then
    assert TriLensCandidates.model_validate_json(response.raw_output)
    assert runner.initial_files == {
        "sanitized_paper.json",
        "rubric.txt",
        "output_schema.json",
        "reviewer_instruction.txt",
    }
    assert runner.command[:4] == (
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
    )
    assert "--ephemeral" in runner.command
    assert "--skip-git-repo-check" in runner.command
    assert (
        runner.command[runner.command.index("--sandbox")],
        runner.command[runner.command.index("--sandbox") + 1],
    ) == ("--sandbox", "read-only")
    assert "gpt-5.6-sol" in runner.command
    assert "compact classifier" not in " ".join(runner.command)
    assert '"default"' not in runner.output_schema_text
    assert runner.call_count == 1


def test_live_mode_rejects_heuristic_only_configuration() -> None:
    # Given / When
    result = CliRunner().invoke(
        app,
        [
            "live",
            "--provider",
            LiveProvider.LOCAL_HEURISTIC.value,
            "--output-dir",
            "runs/live/rejected",
        ],
    )

    # Then
    assert result.exit_code == 1
    assert "LIVE_PROVIDER_UNAVAILABLE" in result.output


def test_codex_structured_output_is_accepted_by_kernel(tmp_path: Path) -> None:
    # Given
    runner = _RecordingRunner()
    kernel = ReviewKernel(
        CodexExecReviewerProvider(runner=runner),
        policy=ReviewKernelPolicy(
            require_reviewer_output=True,
            retry_reviewer_failures=False,
        ),
    )
    assignment = TrustedAssignment(paper_id="CODEX-001", pdf_path=CLEAN_PDF)

    # When
    result = anyio.run(kernel.review, assignment, ReviewMode.FAST, tmp_path)

    # Then
    assert ReviewSubmission.model_validate(result.model_dump()) == result
    assert result.paper_id == assignment.paper_id
    assert runner.call_count == 1


def test_two_paper_codex_smoke_is_concurrent_without_submission(
    tmp_path: Path,
) -> None:
    # Given
    runner = _ConcurrentRunner()
    kernel = ReviewKernel(
        CodexExecReviewerProvider(runner=runner),
        policy=ReviewKernelPolicy(
            require_reviewer_output=True,
            retry_reviewer_failures=False,
        ),
    )
    limiter = anyio.CapacityLimiter(2)
    results: list[ReviewSubmission] = []

    async def scenario() -> None:
        async def review(paper_id: str) -> None:
            result = await kernel.review(
                TrustedAssignment(paper_id=paper_id, pdf_path=CLEAN_PDF),
                ReviewMode.FAST,
                tmp_path,
                limiter,
            )
            results.append(result)

        async with anyio.create_task_group() as task_group:
            _ = task_group.start_soon(review, "CODEX-SMOKE-1")
            _ = task_group.start_soon(review, "CODEX-SMOKE-2")
            await runner.ready.wait()
            runner.release.set()

    # When
    anyio.run(scenario)

    # Then
    assert runner.call_count == 2
    assert runner.max_active == 2
    assert {result.paper_id for result in results} == {
        "CODEX-SMOKE-1",
        "CODEX-SMOKE-2",
    }


@pytest.mark.skipif(
    os.getenv("RUN_CODEX_EXEC_SMOKE") != "1",
    reason="real Codex smoke is opt-in",
)
def test_real_codex_exec_provider_smoke() -> None:
    # Given / When
    response = anyio.run(CodexExecReviewerProvider().review, _request())

    # Then
    parsed = TriLensCandidates.model_validate_json(response.raw_output)
    assert parsed.summary
