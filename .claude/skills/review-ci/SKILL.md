---
name: review-ci
description: Diagnose CI failures or merge blockers using available CI tools and provider logs.
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

# Review CI

Resolve the requested PR, branch, or commit before querying CI. For a PR, use its current head
SHA, which may differ from the local checkout. Correlate each result with its SHA and attempt;
a failed older attempt does not establish the status of the latest run.

## Available evidence

Prefer connected Datadog CI/test/log tools when available. Discover their current names and
schemas instead of requiring a particular MCP namespace. Missing tools should not prevent
reading available GitHub checks or provider logs; state what evidence is unavailable.

```bash
gh pr view <pr> --json headRefOid,headRefName,statusCheckRollup
gh run list --commit <sha> --json databaseId,name,status,conclusion
gh run view <run-id> --log-failed
```

For GitLab jobs, use connected tools or the authenticated GitLab API and the job's actual
project/job IDs. Use the locally configured authentication flow; do not print credentials.
Do not require installing a different agent client to obtain CI status.

## Datadog queries

Use the repository URL `https://github.com/DataDog/dd-trace-py` for PR tools and
`github.com/datadog/dd-trace-py` for coverage tools. Start with the relevant run's time window;
expand it when evidence is missing.

| Evidence | Query / fields |
|----------|----------------|
| Failed jobs | `@git.commit.sha:<sha> @ci.status:error`, job level |
| Failed tests | `@git.commit.sha:<sha> @test.status:fail`, test level |
| Job logs | `@ci.job.id:<job-id>`, job start through end plus ingestion allowance |
| PR insights | Repository URL and PR number; supplement, not a complete check inventory |

Paginate when results are truncated. For missing logs, try other available storage tiers or
provider logs before concluding they are absent. Read build, formatting, and changelog failures
as well as test failures.

Check test events even when jobs passed if those events are available: retries and known-flaky
suppression can hide failures at job level. A `test.is_known_flaky` or `test.is_flaky` label is a
clue, not proof that this occurrence is unrelated. Compare the failure signature and history,
especially if the diff touches the failing path. Distinguish confirmed pre-existing failures,
new regressions, and unresolved attribution.

## Merge queue

The merge queue may test a working branch rather than the PR head. When relevant and logs are
available, query `@pr_number:<pr> service:mergequeue-worker` and inspect status,
`workingBranch.name`, and the working SHA. Keep attempts separate.

Query CI by the resolved working SHA or branch; do not assume the pipeline ID in queue logs
is the identifier used by CI event search. Check GitHub Actions on that branch too.
No matching queue logs means queue status is unknown from this source, not that the PR was never queued.

## Report

State the revision and latest known status, then list actionable failures with log evidence,
likely cause, and next action. Separate missing evidence, pending checks, and historical failures.
Use the `lint`, `run-tests`, and `releasenote` workflows for suggested fixes; do not prescribe raw
formatter commands that conflict with `AGENTS.md`. Include coverage when requested or relevant.
