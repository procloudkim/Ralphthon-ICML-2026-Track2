# Event Context and Source of Truth

## Confirmed operating facts

- Event date: 2026-07-12
- Track: Track 2, Review Agent
- Research specification: 11:00-12:30 Asia/Seoul
- Ralph Loop, hands off: 12:30-15:30
- Human polish and final submission: 15:30-16:30
- Submission hard cut: 16:30
- Production review window: 16:35-17:00
- Track 2 receives approximately ten papers to review
- Track 2 submits an anonymous technical report, title, abstract, GitHub repository, and short run instructions
- Technical report: maximum four pages, hard limit
- Official review fields: Soundness, Presentation, Significance, Originality, Overall Recommendation, Confidence, Comment
- Evaluation includes approach quality and similarity or correlation between human judge scores and agent scores
- The exact human judge heuristics, scores, correlation formula, and metric weight are not available during development
- Assignments and submissions are expected to be handled through an API, with Codex orchestrating the local reviewer

## Source-of-truth precedence

1. Final live event API schema and organizer announcement
2. Current participant guide
3. This repository's frozen contracts
4. Morning transcripts
5. Conversation assumptions

When a final event instruction changes a contract, isolate the change in configuration or `api_adapter.py`, record it in `PROGRESS.md`, and avoid changing the scientific review policy unless required.

## Important project resources

- Ralphthon ICML Auto Research skills repository: `https://github.com/team-attention/ralphthon-icml`
- Submission and review platform: `https://openagentreview.org/`
- Morning session deck: `https://ralphthon-icml-presentation.team-attention.com/slides.bi.html`
- Participant repository: `https://github.com/procloudkim/Ralphthon-ICML-2026-Track2.git`
- Official ICML reviewer instructions: `https://icml.cc/Conferences/2026/ReviewerInstructions`

## Known local environment

- AMD Ryzen AI MAX+ 392, 12 cores / 24 threads
- 64 GB LPDDR5X memory
- AMD Radeon 8060S integrated graphics

Do not assume GPU model inference is available or beneficial. Prefer external model API calls unless a prewarmed local or remote inference setup is already proven before the hands-off window.

## Operational interpretation

The stricter published 25-minute review window governs engineering. Ten papers imply bounded paper-level concurrency, concurrent reviewer perspectives, streaming results, and a fast fallback mode. The system must be able to finish without frontend, database, browser automation, or cloud infrastructure.
