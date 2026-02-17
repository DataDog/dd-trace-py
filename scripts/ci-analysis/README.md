# CI Analysis Scripts

Scripts used to measure the impact of narrowing `profiling_native` trigger rules
in `.gitlab-ci.yml` (PR #16501).

## Prerequisites

```bash
export GITLAB_TOKEN=glpat-...  # read_api scope on gitlab.ddbuild.io
```

## Usage

### 1. Find commits that would be skipped

```bash
python3 find_skippable_commits.py --since 2026-01-01 --until 2026-02-13
```

Compares old (directory-glob) vs new (extension-based) trigger rules against
every commit on `main` in the date range. Writes skippable SHAs to
`/tmp/skipped_shas.txt`.

### 2. Fetch actual job durations for those commits

```bash
python3 fetch_job_durations.py
```

Resolves each SHA to a GitLab pipeline, fetches all `profiling_native` jobs,
and reports per-pipeline and aggregate CI-time.

### 3. Inspect a single pipeline

```bash
python3 fetch_single_pipeline.py 96940191
```

Lists all `profiling_native` jobs and their durations for one pipeline.

## Results (Jan 1 – Feb 12, 2026)

| Metric | Value |
|--------|-------|
| Commits on main | 381 |
| Would-be-skipped triggers | 37 (with jobs) |
| Total unnecessary jobs | 1,212 |
| Total CI-time wasted | 144.6 hours |
| Annualized | ~1,254 CI-hours/year |
