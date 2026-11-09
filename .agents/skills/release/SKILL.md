---
name: release
description: >
  Drive dd-trace-py patch, RC, and final releases with scripts/gh/create_gh_release.py.
  Use when asked to patch a PR into a fix release, create an RC, ship X.Y.0, or run
  post-release after a maintainer publishes.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

## When to use

| Phrase | Command |
|--------|---------|
| "patch PR N into a fix release" / "cut a patch for #N" | `scripts/gh/create_gh_release.py patch N [--branch X.Y]` |
| "create RC for the next release" / "cut an RC" | `scripts/gh/create_gh_release.py rc [--candidate SHA]` |
| "ship X.Y.0" / "final release for X.Y" | `scripts/gh/create_gh_release.py final X.Y` |
| "post-release" / "version is published, continue" | `scripts/gh/create_gh_release.py post-release vX.Y.Z` |
| "wait for PyPI" / "tag pipeline follow-up" | `scripts/gh/create_gh_release.py wait-pypi vX.Y.Z` |

Building blocks (`bump-rc`, `ensure-branch`, `draft-release`) are also available; prefer the intent commands above.

## How to run

1. Re-run the **same** intent command until it prints `WAITING ON: ...` (exit 2) or completes verification.
2. Report the gate plainly to the human. Do not invent workarounds past tag rulesets, reviews, or auth.
3. Prefer `--dry-run` first when inspecting live GitHub state without creating remotes/PRs/drafts.
4. Never publish a GitHub release. Never push release branches or tags unless the human explicitly asks in that message. The script stops at a **draft**.
5. Never post to Slack as the user unless they explicitly ask in that message. Default public ping is `#apm-python-release` via `--slack` (prefer **Incoming Webhook** `hooks.slack.com/services/...` in `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` or SSM `ci.dd-trace-py.slack-release-webhook-prod`; legacy env `DD_RELEASE_SLACK_WEBHOOK_URL_PROD` still works). Workflow `triggers/` is interim-only: script POSTs structured fields after the Workflow is rebuilt (`scripts/gh/SLACK_WORKFLOW_INTERIM.md`); freeform mrkdwn on triggers/ is refused. `--slack-channel guild` is an optional escape hatch. `--test-dm` needs `DDTRACE_RELEASE_DM_URL` (DM only). Prefer printing the review text and stopping when unsure.
6. `dogfood` and `experiment` are **not implemented** yet — print the checklist the script emits and stop.

## Slack announce (when the human asks)

Prefer Incoming Webhook (`services/`). Workflow `triggers/` needs a rebuilt structured Workflow — see `scripts/gh/SLACK_WORKFLOW_INTERIM.md` and `.env.local.example`.

**Slack is for draft-ready / Publish coordination only** — not for the pyproject bump PR. Bump review stays on GitHub (CODEOWNERS on `pyproject.toml` → `@DataDog/python-guild`). Do not also `@dd-trace-py-releasers` in Slack for the bump; that double-pings the same people who get the later draft announce.

```bash
# DM-only dry path (Vlad webhook; no channel post)
DDTRACE_RELEASE_DM_URL=… \
  scripts/gh/create_gh_release.py draft-release --target X.Y --version … \
  --test-dm

# Public review ping → #apm-python-release (after tip pyproject matches + draft exists)
DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL=https://hooks.slack.com/services/… \
  scripts/gh/create_gh_release.py rc --slack
# or: … patch N --branch X.Y --slack
# or: … draft-release … --slack   # --slack-channel defaults to prod
# optional: --slack-precheck  # dual ping+announce; off by default
```

## Human gates (typical) — order matters

1. **Bump PR (release-run step 1):** `patch` / `draft-release` open or reuse a **ready-for-review** (not draft) bump PR into the release branch (`pyproject.toml` → exact tag version, e.g. `4.16.4`) and exit `WAITING ON` merge when tip ≠ version — before CI wait, GitHub release draft, Slack, or Publish. GitLab `package version` requires `CI_COMMIT_TAG == v{pyproject}`; mismatch blocks PyPI. Reviewers come from CODEOWNERS (`DataDog/python-guild` on `pyproject.toml`); **no Slack** for the bump — channel ping is draft-ready only.
2. Fix / backport PR review and merge (bump can land in parallel once the cut is started).
3. CI green on `origin/X.Y` (or candidate SHA) — wet `patch`/`rc`/`final`/`draft-release` poll by default (`--wait-ci`, ~75m / 45s); `--dry-run` never waits; `--no-wait-ci` exits `WAITING ON` immediately if still pending
4. Maintainer publish of the draft (tag ruleset) — creates the Git tag only
5. GitLab **tag** pipeline green (`release_pypi_prod` is `on_success`); branch CI before draft is not enough
6. PyPI listing `ddtrace==X.Y.Z` (`post-release` / `wait-pypi` poll by default, ~90m / 60s; `--no-wait-pypi` one-shots; on timeout: tag-pipeline URL + Retry guidance)
7. Dogfooding PRs in dd-source / dogweb (manual until Phase A)

## References

- `docs/contributing-release.rst`
- `scripts/gh/create_gh_release.py --help`
- `scripts/format_release_notes.py`
- Release Procedure review text: `:headsup: The draft release for <VERSION> is ready for review. Can I get a :lgtm:? <LINK>`
