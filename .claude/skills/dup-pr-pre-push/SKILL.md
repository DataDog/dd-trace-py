---
name: dup-pr-pre-push
description: >-
  Search GitHub for existing open, draft, and recently merged PRs that already
  address the same Python profiling issue or feature, and warn before the first
  push of a new remote branch. Use when pushing a new branch whose changed
  files are Python profiling, or when the user mentions duplicate PRs, existing
  work, already landed, or dup-pr-pre-push. No-op when the diff is outside
  Python profiling. Expanding to all of dd-trace-py is a later flip, not now.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Duplicate PR check before first push (Python profiling only)

Before the **first** `git push` of a branch that has no remote tracking
branch, if the **changed files** are Python profiling, search this repo
for existing PRs on the same issue or feature. Warn the user with URLs.
Do not silently abandon their work. Do not auto-close anything.

If the diff is **outside** those paths: **no-op**. Do not search. Do not
warn. Expanding to all of dd-trace-py is a later flip, not now.

Also run this check before `gh pr create --draft` if it has not run yet.

This does **not** authorize opening a ready PR. Draft only if the user
asked for a PR in the current message.

## Scope (verified)

Applies only in `DataDog/dd-trace-py`. The path list below is the
current scope (suitespec `@profiling`, `tests/profiling/suitespec.yml`,
and the profiling-owned workflow / SLO files). Do not invent extras.

A path counts if it matches any of:

- `ddtrace/profiling/**`
- `ddtrace/internal/datadog/profiling/**`
- `tests/profiling/**` (includes `tests/profiling/suitespec.yml`)
- `ddtrace/internal/settings/profiling.py`
- `ddtrace/internal/settings/profiling.pyi`
- `ddtrace/_trace/processor/endpoint_call_counter.py`
- `.github/workflows/prof-*.yml` (today: `prof-correctness.yml`)
- `.github/workflows/profiling-native.yml`
- `.github/workflows/pytorch_gpu_tests.yml`
- `.gitlab/tests/profiling.yml`
- `.gitlab/benchmarks/slos/profiling-python.yml`

Not in scope (shared or not profiling-specific): root
`tests/suitespec.yml`, `tests/requirements_locks/**`,
`.gitlab/native.yml`, `.gitlab/benchmarks/macrobenchmarks.yml`,
crashtracker, `/cmake/`, IAST, tracer, CI chores.

## When this fires

Run the check when **all** of these are true:

- About to `git push` / `git push -u origin HEAD` and `@{u}` is unset
  (or about to `gh pr create --draft` and this check has not run)
- `git diff --name-only $(git merge-base origin/main HEAD)...HEAD` hits
  a path above

Skip (no-op) when:

- No changed file is in the list above
- Repo is not `DataDog/dd-trace-py`
- The branch already has a remote (`git rev-parse -q --verify @{u}`)
- `main` / `master` / `1.x`

## Workflow

1. Confirm scope. If not profiling, stop. Print nothing about other PRs.
2. Collect terms from the branch name, `git log` / `git diff --stat` vs
   merge-base, linked issues (`#N`, `PROF-N`, JIRA), and what the user
   asked for. Drop filler (`fix`, `chore`, `feat`, `wip`, `the`).
3. Run the helper (execute it; extra keywords as args):

   ```bash
   scripts/search-dup-prs.sh [keyword ...]
   ```

   The helper exits 0 with `## scope: skip` when the diff is not
   profiling. Then refine with `gh` only if it searched:

   ```bash
   gh pr list --state open --limit 50 --json number,title,url,isDraft,headRefName
   gh search prs --repo DataDog/dd-trace-py --state open --limit 20 KEYWORDS
   gh search prs --repo DataDog/dd-trace-py --merged --merged-at ">=YYYY-MM-DD" --limit 20 KEYWORDS
   ```

   Open + draft are required. Merged in the last **~90 days** is a
   "already shipped" warning. Pass distinctive feature words as extra
   args — do not dump the whole commit log. If you have an issue number
   (`#N`, `PROF-N`), pass it; the helper also searches those without
   the 90-day cap.
4. Score (this is your job, not the script's):

   | Strength | What counts | Action |
   | --- | --- | --- |
   | **Strong** | Same issue number; same distinctive title phrase; same core files **and** same feature | **STOP.** Show URLs. Do not push. |
   | **Weak** | Overlapping files only, or generic words | Mention URLs. Continue if they already asked to push. |
   | **Merged strong** | Same issue/feature already merged | Warn `already landed in #N` and stop. Do not re-do it. |
   | None | No real overlap | Push as usual. |

5. Tell the user. Include each match's number, title, state (open / draft /
   merged), and URL. Do not delete local commits. Do not close the other PR.

If `gh` is 401/403, say so and **do not push** until they can search — a
failed search is not a clean bill of health.

## Examples

**Already landed (stop):** new branch fixes heap-live overcount under
`ddtrace/profiling/**`. Closed `#19293` and merged `#18659` already
cover it. Warn `already landed in #18659`.

**Open/draft duplicate (stop):** your branch and `#19293` both fix
heap-live overcount. Show the URL. Ask whether to continue, switch to
that branch, or drop the new one. Do not close `#19293`.

**No-op:** riot lock headers, suitespec hook, IAST, tracer. Those files
are not in the list. Do not search. (Expanding to all of dd-trace-py
is a later flip.)

**Not this skill:** opening a ready PR, auto-closing "stale" work, or
gating a non-profiling first push.
