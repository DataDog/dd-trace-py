---
name: review-ci
description: >
  Review CI results for the current branch, commit, or PR using the Datadog MCP.
  Use this when CI is failing, to understand what's blocking a PR, or to get
  actionable fix instructions for failed jobs and tests.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - mcp__datadog-mcp__get_prs_by_head_branch
  - mcp__datadog-mcp__search_pr_insights
  - mcp__datadog-mcp__search_datadog_ci_pipeline_events
  - mcp__datadog-mcp__aggregate_datadog_ci_pipeline_events
  - mcp__datadog-mcp__search_datadog_test_events
  - mcp__datadog-mcp__aggregate_datadog_test_events
  - mcp__datadog-mcp__get_datadog_flaky_tests
  - mcp__datadog-mcp__search_datadog_logs
  - mcp__datadog-mcp__get_datadog_code_coverage_branch_summary
  - mcp__datadog-mcp__get_datadog_code_coverage_commit_summary
---

# CI Review Skill

Review CI results for a branch, commit, or PR using the Datadog MCP. Identifies
failed jobs, failed tests, and known flaky tests, then produces actionable fix
instructions for an AI coding agent or developer.

## Prerequisite: Datadog MCP

This skill requires the Datadog MCP. Check if `mcp__datadog-mcp__*` tools are
available in your tool list before proceeding. If they are NOT available, stop
and instruct the user:

```
claude mcp add --transport http datadog-mcp \
  "https://mcp.datadoghq.com/api/unstable/mcp-server/mcp?toolsets=core,software-delivery"
```

See https://docs.datadoghq.com/bits_ai/mcp_server/ for setup details.
Authentication uses the user's existing Datadog credentials — run `claude mcp`
to verify the connection is authenticated.

## Repository constants

```
REPO_URL = https://github.com/DataDog/dd-trace-py
REPO_ID  = github.com/datadog/dd-trace-py   # lowercase, no scheme — for coverage tools
```

## Step 0: Resolve the target

If the user did not supply a branch, commit SHA, or PR number, resolve from git:

```bash
git rev-parse --abbrev-ref HEAD   # branch
git rev-parse HEAD                # full commit SHA
```

Save both values — they are used in every subsequent step.

## Step 1: Find the PR (if not already known)

If you have a branch but no PR number, call `get_prs_by_head_branch`:

```
repo_url    = https://github.com/DataDog/dd-trace-py
head_branch = <branch>
```

Pick the most recent open PR. A PR number is needed for `search_pr_insights`.

## Step 1.5: Check Merge Queue status (if PR number is known)

The merge queue is an **internal system** (not GitHub's built-in merge queue). When a
PR is queued, a working branch like `mq-working-branch-main-4839cf6` is created and CI
runs on that branch — not on the PR's head branch. This step finds working branch details
so Steps 2–4 can also check for failures there.

Query `search_datadog_logs` for MQ worker logs:

```
query   = @pr_number:<pr_number> service:mergequeue-worker -"WaitingCron" -"not mergeable yet"
from    = now-7d
sort    = timestamp   (oldest first, to see full attempt history)
```

Key fields to extract from the log attributes (`extra_fields` if needed):
- `status` — `queued`, `in_progress`, `rejected`, `merged`
- `workingBranch.name` — e.g. `mq-working-branch-main-4839cf6`
- `WorkflowType` — look for `BuildWorkingBranch` (attempt start) and `UpdateMergeRequestStatus` (outcome)
- `head_sha` / working branch SHA — for CI queries

**Important**: The `gitlab_pipeline_id` / `@ci.pipeline.id` from MQ logs does NOT match
Datadog CI pipeline events. Always use `@git.branch:<working_branch_name>` or
`@git.commit.sha:<working_branch_sha>` to find CI results for a working branch.

If MQ logs are found:
1. Note all working branch names from past/current attempts
2. In Steps 2–4, query for failures on the working branch in addition to the PR's head commit:
   - `@git.branch:<working_branch_name> @ci.status:error`
   - `@git.commit.sha:<working_branch_sha> @test.status:fail`
3. For GitHub Actions on the working branch: `gh run list --branch <working_branch_name>`

If no MQ logs are found, the PR has not been queued — skip this step and proceed.

## Step 2: Get failed CI jobs

Call `search_datadog_ci_pipeline_events` with `ci_level=job` to find failed jobs.

**For the PR's head commit:**
```
query    = @git.commit.sha:<full_sha> @ci.status:error
ci_level = job
from     = now-7d
sort     = -timestamp
```

**If a working branch was found in Step 1.5, also query by branch:**
```
query    = @git.branch:<working_branch_name> @ci.status:error
ci_level = job
from     = now-7d
sort     = -timestamp
```

Note the `pipeline_name` and `job_name` for each failure:
- Pipeline names like `DataDog/apm-reliability/dd-trace-py` → **GitLab CI**
- Workflow names like `Changelog`, `Build`, `System Tests` → **GitHub Actions**

The provider tells you where to get logs (Step 3).

## Step 3: Get detailed logs for each failed job

### GitLab CI jobs

Call `search_datadog_logs` with `@ci.job.id:<job_id>`, time window from job
start to end+1h:

```
query = @ci.job.id:<job_id>
from  = <job start timestamp>
to    = <job end timestamp + 1 hour>
```

Parse logs for: compiler errors, formatting diffs, test assertion failures,
specific file paths and line numbers. Sort oldest-first (`sort=timestamp`) to
read the failure at the end.

If `search_datadog_logs` returns 0 results, retry with `storage_tier=flex_and_indexes`.

### GitHub Actions jobs

Logs for GitHub Actions are NOT indexed in Datadog. Use `gh` CLI instead:

```bash
# Get run IDs for the PR's head commit
gh run list --commit <sha> --json databaseId,name,status,conclusion

# If a working branch was found in Step 1.5, also check it
gh run list --branch <working_branch_name> --json databaseId,name,status,conclusion

# Get logs for a specific failed run
gh run view <databaseId> --log-failed
```

Match failed runs by `name` to the job names from Step 2.

## Step 4: Get failed test events (always run — even if all jobs passed)

Call `search_datadog_test_events` filtered by commit SHA. If a working branch was
found in Step 1.5, run both queries:

```
# PR's head commit
query      = @git.commit.sha:<head_sha> @test.status:fail
test_level = test
from       = now-7d
page_limit = 10

# Working branch (if found in Step 1.5)
query      = @git.commit.sha:<working_branch_sha> @test.status:fail
test_level = test
from       = now-7d
page_limit = 10
```

**Important**: Tests can fail without failing the CI job (known-flaky tests are
suppressed). Always run this step regardless of pipeline status.

Key fields to extract from each failing test:
- `test.name`, `test.suite`, `test.module` — what failed
- `test.source.file` + `test.source.start`/`end` — exact location
- `error.message` + `error.stack` — the actual failure
- `test.is_known_flaky` / `test.is_flaky` — if `true`, this is pre-existing noise
- `ci.provider.name`, `ci.job.name` — which CI job ran it

## Step 5: PR insights (supplement)

If a PR number is known, call `search_pr_insights`:

```
repo_url  = https://github.com/DataDog/dd-trace-py
pr_number = <pr_number>
```

This supplements Step 2–4 but only lights up for test-level failures — it is
silent for build/format/changelog failures.

## Step 6: Present the actionable summary

Structure the output as follows:

### Merge Queue status (if applicable)

If MQ logs were found in Step 1.5, show a brief history at the top:

```
**Merge Queue**: Attempt #1 rejected at 14:07 (checks failed). Attempt #2 in progress since 14:14.
Working branch: mq-working-branch-main-4839cf6
```

If the PR has not been queued, omit this section entirely.

### Overall status

One line: "X job failures, Y test failures (Z known-flaky), A GitHub Actions failures."
Or: "All CI passed. N known-flaky test failures — no action needed."
If failures are on the working branch (not the PR head), note: "(on MQ working branch)".

### Failed jobs (new/actionable)

For each non-flaky job failure:

```
**[Job name]** (GitLab/GitHub, <duration>s)
Root cause: <extracted error from logs>
Files affected: <file paths and line numbers>
Fix: <concrete command or action>
```

Examples of good fix instructions:
- "Run `cargo fmt` in `src/native/` to fix Rust formatting"
- "Run `ruff format ddtrace/internal/native/_native.pyi`"
- "Run `reno new <slug>` to add a release note, or apply `changelog/no-changelog` label"
- "Fix assertion at `tests/foo/test_bar.py:42`: expected X, got Y"

### Known-flaky test failures (noise)

List them briefly so the user is aware, but mark clearly as pre-existing:

```
Known-flaky (no action needed):
- test_foo_bar[py3.12] in tests/internal/test_foo.py:38 — assert 'x' == 'y'
```

### Coverage (if requested)

Only include if the user asked about coverage or if patch coverage dropped
significantly. Use `get_datadog_code_coverage_commit_summary` or
`get_datadog_code_coverage_branch_summary`.

## Quick reference: when to use each tool

| Situation | Tool |
|-----------|------|
| Find PR for a branch | `get_prs_by_head_branch` |
| Is this PR in the merge queue? | `search_datadog_logs` (service:mergequeue-worker @pr_number) |
| What's the MQ working branch? | `workingBranch.name` field in MQ logs |
| Which jobs are failing? | `search_datadog_ci_pipeline_events` (ci_level=job) |
| Why did a GitLab job fail? | `search_datadog_logs` (@ci.job.id) |
| Why did a GitHub Actions job fail? | `gh run view <id> --log-failed` |
| GitHub Actions on MQ working branch? | `gh run list --branch <working_branch_name>` |
| Which tests are failing? | `search_datadog_test_events` |
| Is this failure a known flaky test? | check `test.is_known_flaky` field in test events |
| What's blocking this PR? | `search_pr_insights` (test failures only) |
| Did coverage drop? | `get_datadog_code_coverage_commit_summary` |
