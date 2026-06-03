# Prism — Claude Code multi-agent project

This repo uses four Claude Code subagents under `.claude/agents/`:

| Agent | Role |
|-------|------|
| **orchestrator** | Project lead — coordinates the team, writes `README.md`, reports to you, handles manual sign-off and final git push |
| **backend-developer** | Senior backend engineer — APIs, domain logic, CLI, data layer |
| **ui-developer** | Frontend engineer — UI wired to the backend contract |
| **tester** | QA — runs backend, UI, and CLI; validates against `PRD.md` |

## How to start

1. Put requirements in [`PRD.md`](PRD.md).
2. In Claude Code, run with the orchestrator as the main agent, or say:

   ```
   Use the orchestrator agent. Read PRD.md and drive the full build cycle.
   ```

3. When tests pass, the orchestrator writes **`README.md`** (overview, setup, run commands, Claude API key).
4. Follow the README and orchestrator instructions to run the app manually.
5. Reply **"looks good"** (or similar) to trigger cleanup, commit, and push.

## Coordination artifacts (during development)

| File | Owner | Purpose |
|------|-------|---------|
| `PRD.md` | You | Requirements baseline |
| `BACKEND_NOTES.md` | backend-developer | API contracts, endpoints, run commands |
| `UI_NOTES.md` | ui-developer | Routes, components, how to start the UI |
| `TEST_RESULTS.md` | tester | Pass/fail, PRD gaps, repro steps |

These coordination files are removed before the final commit unless you ask to keep them.

## Agent definitions

- [`.claude/agents/orchestrator.md`](.claude/agents/orchestrator.md)
- [`.claude/agents/backend-developer.md`](.claude/agents/backend-developer.md)
- [`.claude/agents/ui-developer.md`](.claude/agents/ui-developer.md)
- [`.claude/agents/tester.md`](.claude/agents/tester.md)
