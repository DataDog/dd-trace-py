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

## Resolve a branch to a PR

If no target was supplied, read the current branch and full SHA:

```bash
git branch --show-current
git rev-parse HEAD
```

For a branch, use a connected PR-search tool scoped to `DataDog/dd-trace-py` and the head
branch, or use:

```bash
gh pr list --repo DataDog/dd-trace-py --head <branch> --state open \
  --json number,url,headRefOid,headRepositoryOwner,baseRefName,updatedAt
```

Match the head repository owner and target base when more than one PR shares the branch name;
do not choose solely by recency. Use the selected PR's remote head SHA for its CI. If no PR is
found, or the checkout is detached, review the supplied/resolved commit and report that PR-specific
evidence is unavailable. Keep local unpushed changes separate from the remote CI result.

## Available evidence

Prefer connected Datadog CI/test/log tools when available. Discover their current names and
schemas instead of requiring a particular MCP namespace. Missing tools should not prevent
reading available GitHub checks or provider logs; state what evidence is unavailable.

```bash
gh pr view <pr> --repo DataDog/dd-trace-py --json url,headRefOid,headRefName,statusCheckRollup
gh run list --repo DataDog/dd-trace-py --commit <sha> \
  --json databaseId,name,headSha,headBranch,attempt,status,conclusion,url
gh run view <run-id> --repo DataDog/dd-trace-py --attempt <attempt> --log-failed
```

For one failing job, `gh run view <run-id> --repo DataDog/dd-trace-py --attempt <attempt>
--job <job-id> --log-failed` narrows the output. Read full logs when the failed-step excerpt lacks
the setup or compiler output needed to explain the failure. Use returned check/run URLs for
provider links; a workflow name alone is not a unique run identifier.

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

Paginate when results are truncated. For missing logs, first check job identity and time range.
Inspect the connected log tool's schema for supported storage-tier options; retry with a broader
tier only when that option is actually available. Record the tier/window searched, and fall back
to provider logs when necessary. Use only enum values accepted by the connected tool's schema.
Read build, formatting, and changelog failures as well as test failures.

Extract these details when the event payload provides them; flattened attributes, nested fields,
and provider-specific names can differ. Missing fields remain unknown:

| Detail | Fields / source |
|--------|-----------------|
| Test identity | `test.module`, `test.suite`, `test.name`, `test.parameters`; preserve parameterization when grouping |
| Test location | `test.source.file`, `test.source.start`, `test.source.end`; verify against the tested revision |
| Failure signature | `error.message`, `error.stack`, plus assertion/compiler output from job logs |
| CI correlation | `ci.provider.name`, `ci.job.name`, job/pipeline IDs, SHA, timestamps and attempt/retry identity |
| Evidence links | Returned `ci.job.url`, `ci.pipeline.url`, check URLs or provider run `url` |

The repository emits these test and CI attribute names in `ddtrace/ext/test.py` and
`ddtrace/ext/ci/__init__.py`; that does not guarantee every search result contains them. Do not
infer the CI provider solely from a job name or manufacture a pytest node ID from unrelated fields.
Use a test's actual node ID from logs when available, with `run-tests` for reproduction.

Check test events even when jobs passed if those events are available: retries and known-flaky
suppression can hide failures at job level. A `test.is_known_flaky` or `test.is_flaky` label is a
clue, not proof that this occurrence is unrelated. Compare the failure signature and history,
especially if the diff touches the failing path. Distinguish confirmed pre-existing failures,
new regressions, and unresolved attribution.

## Merge queue

The merge queue may test a working branch rather than the PR head. When relevant and logs are
available, query `@pr_number:<pr> service:mergequeue-worker` and inspect status,
`workingBranch.name`, and the working SHA. Keep attempts separate.

Inspect returned attributes for `WorkflowType`, `status`, `workingBranch.name`, `head_sha`,
and any separately identified working-branch SHA. These are discovery leads, not a guaranteed
schema: confirm each field's meaning from the current payload/provider evidence. In particular,
do not assume `head_sha` means the working SHA, or infer an outcome from an unfamiliar workflow
type/status value. Record unknown values verbatim with their timestamps.

Read queue events chronologically and group them by explicit attempt/workflow identifiers or
working branch. Start without message exclusions. If repeated wait messages dominate the results,
filter only after inspecting them and retain the unfiltered attempt/outcome evidence. A missing
outcome does not imply success or rejection.

Query CI by the resolved working SHA or branch; do not assume the pipeline ID in queue logs
is the identifier used by CI event search. Check GitHub Actions on that branch too.
Use `gh run list --repo DataDog/dd-trace-py --branch <working-branch>` to discover runs, then
verify each run's `headSha` and attempt before attributing failures to the queue attempt.
No matching queue logs means queue status is unknown from this source, not that the PR was never queued.

## Report

State the revision and latest known status, then list actionable failures with log evidence,
likely cause, and next action. Separate missing evidence, pending checks, and historical failures.
Use the `lint`, `run-tests`, and `releasenote` workflows for suggested fixes; do not prescribe raw
formatter commands that conflict with `AGENTS.md`. Include coverage when requested or relevant.
