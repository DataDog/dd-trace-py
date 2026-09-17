# Repo context — dd-trace-py

Read only by the orchestrator (Step 0 of `SKILL.md`), not by individual reviewers. Repo-specific; not part of the shared core.

## Related skills in this repo

Existing skills live under `.claude/skills/` as real directories (lint, run-tests, releasenote, apm-integrations, review-ci, …). This review skill is the first under `.agents/skills/`; `.claude/skills/dd-apm-sdk-review` is a symlink to it. No name clash.

Cite the others as authoritative for their area. Do not invoke them, and they must not invoke this skill:

- `run-tests` / `lint` — how to execute tests and format code
- `apm-integrations` / `llmobs-integrations` — how to author contrib / LLMObs integrations
- `review-ci` — CI failure triage via Datadog MCP, not a product-code review
