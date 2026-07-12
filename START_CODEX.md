# Start Codex

## 1. Initialize and inspect

From the repository root, initialize Git if necessary, create the initial commit, and start a new Codex session.

Ask Codex first:

    Read AGENTS.md and the files it routes to. Summarize the active constraints, hands-off schedule, hidden-human-evaluator limitation, prompt-injection trust boundary, 25-minute production requirement, and definition of done. Do not edit files.

Verify the summary before 12:30.

## 2. Start the hands-off goal

At 12:30, paste the contents of `RALPH_GOAL.md` as the `/goal` objective.

The goal is deliberately concise because Codex goal objectives have a length limit. Detailed instructions live in `.agent/RALPHTHON_EXECPLAN.md`.

## 3. Human phase at 15:30

Review:

- `PROGRESS.md`
- `.agent/RALPHTHON_EXECPLAN.md`, especially outcomes and limitations
- test and evaluation artifacts
- README commands
- Git history and working tree
- technical report source and generated PDF

Fix only blocking defects and report clarity. Freeze code, prompts, model, rubric, and concurrency by the times in the ExecPlan.
