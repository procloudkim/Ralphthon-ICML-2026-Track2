# Codex Preflight for the Hands-Off Run

Complete this before 12:30 Asia/Seoul.

## Repository setup

1. Place this canonical seed at the Git repository root.
2. Initialize Git if needed and connect the intended remote.
3. Confirm `.gitignore` excludes secrets, private papers, runs, and environment files.
4. Create a clean initial commit.
5. Start Codex from the repository root so the root `AGENTS.md` is discovered.

## Verify instructions

Start a new Codex session and ask it to summarize the active repository instructions without editing files. Codex loads project instructions once per run, so restart the session after changing `AGENTS.md`.

An official verification pattern is equivalent to:

    codex --ask-for-approval never "Summarize the current repository instructions without editing files."

Confirm the response mentions:

- hidden human labels;
- 12:30-15:30 hands-off period;
- ten papers in 25 minutes;
- prompt-injection boundary;
- no frontend, database, or cloud infrastructure;
- use of the ExecPlan.

## Goal mode

If `/goal` is unavailable, enable the goals feature using the current Codex CLI feature command or configuration, then restart Codex. The goal itself must remain concise; detailed instructions live in the ExecPlan.

At 12:30, set `/goal` using the contents of `RALPH_GOAL.md`.

## Permissions and sandbox

Use the narrowest permissions that still allow repository edits, tests, and local Git operations. For a trusted version-controlled repository, workspace-write with an appropriate approval profile is preferred over unrestricted full access.

For a hands-off run, choose a permission profile that does not pause for routine repository commands, but do not disable the sandbox or expose unrelated filesystem paths, credentials, or arbitrary network access.

Only allow external network access that is required for model APIs, approved package installation, or Git push. Complete dependency installation and authentication before 12:30 where possible.

## Environment checks

Before the loop, verify:

- Python and package manager are available;
- model credentials work without exposing them to logs;
- Git identity and remote access work;
- `$gitmaster` is available if required, or the fallback commit/push workflow is known;
- the machine will not sleep;
- the project directory is writable;
- the system clock and Asia/Seoul deadline assumptions are correct;
- a sample model call and a trivial test command succeed.

## Production prewarm, 16:30-16:35

Load the frozen configuration, import modules, initialize clients, validate credentials, create queues and semaphores, verify output directories, run a schema-only smoke test, and confirm deadline tracking. Do not change prompts, rubric, models, or architecture after freeze.
