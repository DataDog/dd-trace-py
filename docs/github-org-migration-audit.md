# GitHub Organization Migration Audit (APMLP-1185)

Inventory of every CI/CD and release-pipeline touchpoint in this repo that is
coupled to an external organization, registry, or GitLab project path that may
change as part of the
[Datadog GitHub organization split](https://datadoghq.atlassian.net/browse/APMLP-1185).

**Maintenance:** Last updated 2026-04-29. Update this file whenever you add a
new reference to an external Datadog repo, internal endpoint, or chainguard
STS policy. CI does not yet enforce this, but the list is used by DevEx to
plan the migration runbook.

---

## Context

Datadog is splitting its GitHub presence into two organizations:

- `github.com/Datadog/` — public / open-source code (stays).
- `github.com/ddoghq/` — internal code, managed under Enterprise Managed
  Users (EMU) for stronger security, auditing, and compliance.

This repo (`dd-trace-py`) is open source and is expected to **stay on
`Datadog/`**. The risk is therefore not in our outbound URL, but in our
inbound coupling: the internal Datadog systems and Datadog-org GitHub repos
that this repo depends on at CI, build, and release time. From a public OSS
workflow, an EMU-org repo is unreachable by default, so any internal
dependency that moves to `ddoghq/` without a public mirror will silently
break this repo's CI.

The Q2 2026 migration deadline means we need an inventory before we know
exactly what moves where. The DevEx / APM Platform team owns the
authoritative runbook (see APMLP-1185 description).

---

## How to use this document

- **DevEx / APM Platform:** for each external dependency below, mark whether
  it stays on `Datadog/`, moves to `ddoghq/`, or gets a new endpoint. The
  "Action when migration confirmed" column is the corresponding mitigation
  in this repo.
- **Engineers adding CI changes:** prefer reusing one of the variables
  defined in `.gitlab/external-refs.yml` (`$EXT_GITLAB_*`,
  `$EXT_GITHUB_*`, `$EXT_HOST_*`). If your case cannot use a variable
  (e.g. `include: - project:` is resolved before variable expansion), add
  the file:line to `.gitlab/external-refs.allowlist.txt` with a
  justification, and update the appropriate table in this document.
- **Reviewers:** the `external-refs-lint` job blocks PRs that introduce a
  hardcoded `gitlab.ddbuild.io/DataDog/` rewrite, a `--scope DataDog/...`
  argument, a `uses: DataDog/...` reference, a new
  `registry.ddbuild.io/...` image, or any of the other patterns listed in
  `scripts/check-external-refs.py`. Patterns are case-insensitive. For
  wrapper composite actions under `.github/actions/`, the `uses:` ref MUST
  be a 40-char SHA — tag/branch pins are rejected. Either reuse a variable,
  fix the wrapper, or allowlist with justification.

---

## Tier 1 — Critical (release pipeline)

Failure here blocks releases / publishing wheels to PyPI / OCI / S3.

| What | Where | Risk | Action when migration confirmed |
|---|---|---|---|
| One-pipeline include from internal GitLab templates | `.gitlab/one-pipeline.locked.yml:4` (auto-generated) | Whole OCI / lib-injection / shared release pipeline | Re-generate via the upstream automation once `gitlab-templates.ddbuild.io` migrates. |
| PyPI publish image and AWS-SSM-bound token | `.gitlab/release.yml:2,25,29,42` | PyPI publish on tag fails | Re-bind the IAM role on the new GitLab project path; confirm SSM parameter `ci.${CI_PROJECT_NAME}.${PYPI_REPOSITORY}_token` is reachable. |
| `dd-sts` exchange for `notify_datadog_release` | `.gitlab/release.yml:60` | Release notification to Datadog backend fails | Update the STS policy `dd-trace-py-gitlab-app-key` if `dd-sts.us1.ddbuild.io` or its issuer change. |
| Wheel upload to S3 (`dd-trace-py-builds`) | `.gitlab/package.yml:61,81,93` and `.gitlab/scripts/upload-wheels-to-s3.sh` | Public install scripts (`curl …/install.sh`) stop receiving new wheels | Re-bind IAM role to the new GitLab project path. |
| Debug-symbol upload via SSM keys | `.gitlab/scripts/upload-debug-symbols-to-backend.sh:14-15` | Crashtracker symbolication breaks for new releases | Re-bind IAM role; confirm SSM names. |
| `DataDog/dd-octo-sts-action` in release-adjacent workflows | `.github/workflows/backport.yml:73`, `generate-supported-versions.yml:74`, `generate-package-versions.yml:91` | Backport, supported-versions, and package-version PR creation breaks | If the action repo moves to `ddoghq/`, replace with a `Datadog/`-hosted mirror or vendor under `.github/actions/`. |

## Tier 2 — High (CI auth and silent reroutes)

Failure here blocks CI on PRs and `main`.

| What | Where | Risk | Action when migration confirmed |
|---|---|---|---|
| Silent URL rewrite `git config --global url."…gitlab.ddbuild.io/DataDog/".insteadOf "https://github.com/DataDog/"` | `.gitlab/benchmarks/microbenchmarks.yml:36,173`, `scripts/codeql_scan.sh:10` | Every subsequent `git clone github.com/DataDog/X` is rerouted; if the GitLab namespace changes, clones fail with confusing "repository not found" errors | Update the rewrite to the new GitLab namespace (e.g. `gitlab.ddbuild.io/ddoghq/`); ideally centralize the rewrite in a shared script. |
| Chainguard STS policies (signed manifests) bind GitLab + GitHub paths | `.github/chainguard/codeql.sts.yaml`, `gitlab.github-access.read.sts.yaml`, `self.backport.create-pr.sts.yaml`, `self.generate-supported-versions.create-pr.sts.yaml`, `self.generate-package-versions.create-pr.sts.yaml` | Token minting for codeql, backport, GH-access-from-GitLab, and PR-creation workflows fails | Update `subject_pattern`, `subject`, `claim_pattern.project_path`, `claim_pattern.ci_config_ref_uri`, and `job_workflow_ref` to the new paths. Coordinate with the security team that owns Chainguard. |
| `dd-octo-sts token --scope DataDog/dd-trace-py --policy gitlab.github-access.read` | `.gitlab-ci.yml:99`, `.gitlab/package.yml:276`, `.gitlab/benchmarks/microbenchmarks.yml:101` | GitHub token issuance from GitLab fails | Update `--scope` to the new GitHub repo path. |
| `dd-octo-sts --scope DataDog/dd-trace-py --policy codeql` | `scripts/codeql_scan.sh:13,22` | CodeQL scan and SARIF upload fail | Same. |
| `dd-octo-sts token --scope DataDog/prof-correctness --policy dd-trace-py.gitlab.trigger-ci` | `.gitlab-ci.yml:511` | Profiling correctness pipeline trigger fails | Update if `prof-correctness` moves to `ddoghq/`. |
| `gh workflow run --repo DataDog/prof-correctness` | `.gitlab-ci.yml:517` | Same as above | Same. |
| `DataDog/system-tests/.github/workflows/system-tests.yml` reusable workflow + `…/.github/actions/push_to_test_optim` | `.github/workflows/system-tests.yml:342,391,421,431` | System tests, serverless system tests, integration-frameworks tests, tracer-release all break | Confirm `system-tests` stays on `Datadog/` (it is OSS); otherwise re-host or vendor the reusable workflow. |
| `DataDog/commit-headless`, `DataDog/dd-sts-action` | `.github/workflows/backport.yml:125`, `.github/workflows/system-tests.yml:336` | Backport push and DD API key fetch break | Confirm these actions stay on `Datadog/`; if they move to `ddoghq/`, vendor or replace. |
| Cross-project GitLab `trigger:` references | `.gitlab-ci.yml:129` (`DataDog/datadog-lambda-python`), `:194` (`DataDog/apm-reliability/apm-sdks-benchmarks`), `:474` (`DataDog/debugger-demos`); `.gitlab/benchmarks/serverless.yml:7` (`DataDog/serverless-tools`) | `serverless lambda tests`, `apm-sdk-benchmarks`, `deploy_to_di_backend`, serverless benchmarks all break | Update GitLab project paths after the namespace migration. |
| Internal GitLab clones via `${CI_JOB_TOKEN}@gitlab.ddbuild.io/DataDog/benchmarking-platform` | `.gitlab/benchmarks/microbenchmarks.yml:38,175`, `.gitlab/benchmarks/macrobenchmarks.yml:61` | Microbenchmarks and macrobenchmarks break | Update GitLab path. |
| `git clone https://github.com/DataDog/codescanning.git` (internal repo) | `scripts/codeql_scan.sh:11` | CodeQL job fails | If `codescanning` moves, the URL rewrite above will reroute it; verify the new GitLab path is correct. |
| `git clone https://github.com/DataDog/system-tests.git` (OSS) | `.gitlab/system-tests.yml:50` | GitLab-side system tests break | Should be unaffected — system-tests is OSS. Verify. |

## Tier 3 — Medium (functionally affected, not release-blocking)

| What | Where | Risk | Action when migration confirmed |
|---|---|---|---|
| Internal container registry (`registry.ddbuild.io/...`) — 40+ refs | `.gitlab/services.yml`, `.gitlab/package.yml`, `.gitlab/testrunner.yml`, `.gitlab/multi-os-tests.yml`, `.gitlab/native.yml`, `.gitlab/system-tests.yml`, `.gitlab/fuzz.yml`, `.gitlab/benchmarks/*.yml`, `.gitlab/release.yml`, `.gitlab-ci.yml`, `docker/Dockerfile.fuzz` | All GitLab CI jobs that pull internal images fail | Update registry hostname or rely on DNS-level redirect. Centralize via a single `IMAGE_BASE_URL`-style variable. |
| Benchmarking ECR `486234852809.dkr.ecr.us-east-1.amazonaws.com` | `.gitlab/benchmarks/microbenchmarks.yml:16`, `.gitlab/benchmarks/macrobenchmarks.yml:18` | Benchmarks break if AWS account moves | Update account ID or use a parameterized variable. |
| `binaries.ddbuild.io`, `pr-commenter.us1.ddbuild.io`, `us1.ddbuild.io` | `.gitlab/scripts/post-pr-comment.sh:22,25`, `.gitlab/fuzz.yml:35`, `.gitlab/scripts/fuzz_infra.py:191,197` | PR comments, fuzzing replication | Update endpoints. |
| `gh run download --repo DataDog/dd-trace-py` (cross-CI artifact handoff) | `.gitlab/download-wheels-from-gh-actions.sh:18,29`, `.gitlab/download-library-version-from-gh-actions.sh:13` | win_arm64 wheel handoff from GH Actions to GitLab | Should be unaffected — repo stays on `Datadog/`. Verify post-migration. |
| `DataDog/datadog-lambda-python` checkout | `.github/workflows/system-tests.yml:369` | Serverless system-tests build | Confirm `datadog-lambda-python` stays on `Datadog/` (it is OSS). |
| Benchmarking platform documentation references | `benchmarks/Dockerfile:4,20` | Cosmetic only (URLs in comments) | Update if `benchmarking-platform` moves. |

## Tier 4 — Low (cosmetic / dev-experience)

| What | Where | Risk | Action when migration confirmed |
|---|---|---|---|
| `scripts/backport` regex matches only `[Dd]ata[Dd]og/dd-trace-py` | `scripts/backport:57,59` | Auto-detection of git remote falls back to `origin` if a developer adds a `ddoghq` remote | Preemptively widened on 2026-04-29 to also accept `ddoghq/dd-trace-py`. |
| `DD_GIT_REPOSITORY_URL` for CI Visibility source links | `.gitlab-ci.yml:24` | Source-code links in Datadog test reports stop resolving | Update if/when repo moves. |
| `https://github.com/DataDog/dd-trace-py` URLs in docs / READMEs / release notes / CHANGELOG / `pyproject.toml` / `setup.py` | many | Cosmetic — these are correct since the OSS repo stays on `Datadog/` | No change unless this repo itself moves. |
| `tests/sourcecode/test_source_code_link.py` validates that source links resolve to `github.com` or `gitlab.ddbuild.io` | `tests/sourcecode/test_source_code_link.py:10` | The test must continue to recognize the new GitLab path | Update the regex / allowlist when GitLab namespace changes. |
| `scripts/update-system-tests-version.py` clones `https://github.com/DataDog/system-tests.git` | line 9 | Dev script breaks if system-tests moves | Confirm `system-tests` stays on `Datadog/`. |
| `scripts/needs_testrun.py` fetches `https://github.com/DataDog/dd-trace-py/pull/{n}` | line 57 | Dev script for selecting test runs | No change unless this repo moves. |

---

## Centralization status

Implemented in this PR (see `.gitlab/external-refs.yml` for the canonical list):

- ✅ Cross-project GitLab `trigger:project:` references — replaced with
  `$EXT_GITLAB_*` variables (4 sites: `.gitlab-ci.yml:129,194,474`,
  `.gitlab/benchmarks/serverless.yml:7`).
- ✅ `dd-octo-sts --scope DataDog/dd-trace-py` — replaced with
  `--scope "$EXT_GITHUB_DD_TRACE_PY"` (4 sites: `.gitlab-ci.yml:99`,
  `.gitlab/package.yml:276`, `.gitlab/benchmarks/microbenchmarks.yml:101`,
  `scripts/codeql_scan.sh:13,22`).
- ✅ `dd-octo-sts --scope DataDog/prof-correctness` and downstream
  `gh workflow run --repo` — replaced with `$EXT_GITHUB_PROF_CORRECTNESS`
  (`.gitlab-ci.yml:511,517`).
- ✅ Three internal Datadog GitHub Actions wrapped under `.github/actions/`:
  `dd-octo-sts-token` (3 callers), `commit-headless` (1 caller),
  `dd-sts-token` (1 caller). Workflows now `uses: ./.github/actions/<name>`
  so the upstream SHA can be updated in one place.
- ✅ Blocking CI lint (`scripts/check-external-refs.py`) wired in as the
  `external-refs-lint` GitLab CI job. Allowlist of unavoidable literals lives
  in `.gitlab/external-refs.allowlist.txt`, each with a justification.

Still outstanding (deferred until the runbook lands):

1. **`registry.ddbuild.io/...` image refs** — already use `IMAGE_BASE_URL` in
   some places (`.gitlab/package.yml:2`); extend to all `.gitlab/` files.
2. **`gitlab.ddbuild.io/DataDog/` URL rewrite** — duplicated across three
   files; consider a `.gitlab/scripts/setup-internal-git-rewrite.sh` helper.
3. **`DataDog/system-tests` ref** — already centralized via `SYSTEM_TESTS_REF`
   in `.gitlab-ci.yml:19`; the `.github/workflows/system-tests.yml` workflow
   still has hardcoded SHAs (`@23941ab…`, `@1e5d6b…`) that drift from
   `SYSTEM_TESTS_REF`. Worth aligning.
4. **Full source vendoring of internal Datadog Actions** — current wrappers
   (`.github/actions/{dd-octo-sts-token,commit-headless,dd-sts-token}`) still
   delegate to `Datadog/<action>@<sha>`. If those action repos move to
   `ddoghq/`, the upstream `uses:` line becomes unreachable from public OSS
   workflows; the wrappers must be replaced with vendored sources.

## Open questions for DevEx / APM Platform

1. Is `dd-trace-py`'s OSS repo confirmed to stay on `github.com/Datadog/`?
2. Does the GitLab project path stay at `DataDog/apm-reliability/dd-trace-py`,
   or does it move (e.g. to `ddoghq/apm-reliability/dd-trace-py`)?
3. Which of these *Datadog-org* GitHub repos are confirmed to stay public,
   and which move to `ddoghq/`?
   - `dd-octo-sts-action`, `commit-headless`, `dd-sts-action`
   - `system-tests`, `datadog-lambda-python`
   - `codescanning`, `prof-correctness`, `debugger-demos`
   - `serverless-tools`, `benchmarking-platform`, `benchmarking-platform-tools`
   - `apm-reliability/apm-sdks-benchmarks`
4. Will `*.ddbuild.io` endpoints (`gitlab`, `registry`, `dd-sts`, `binaries`,
   `pr-commenter`, `gitlab-templates`) keep their hostnames, or will they be
   renamed as part of the cluster split?
5. Will the benchmarking ECR account `486234852809` change?
6. For Chainguard STS policies, who owns updating the manifests once GitLab
   and GitHub paths change? Is there a coordinated rollout or do we update
   them ourselves?
7. For `dd-octo-sts`, how do scope changes interact with policy validation?
   Do we need to land scope updates and policy updates atomically?

---

## Phased mitigation plan

### Phase 0 — Coordination (in progress)

- Obtain the official migration runbook from DevEx (Dmytro Yurchenko).
- Resolve the open questions above.
- Confirm migration timeline so changes can be staged with each phase.

### Phase 1 — Defense in depth (safe to do today)

- [x] `scripts/backport`: accept `ddoghq/dd-trace-py` remote in addition to
  `Datadog/dd-trace-py`. Keeps the dev script working when developers add
  internal remotes.
- [x] Add this audit document and link it from `docs/contributing-release.rst`
  and `docs/contributing.rst`.
- [x] Centralize cross-project GitLab triggers and `dd-octo-sts --scope`
  arguments into `.gitlab/external-refs.yml` (variable-based for the cases
  that GitLab CI can expand at run time; allowlisted in
  `.gitlab/external-refs.allowlist.txt` for the cases that cannot, e.g.
  `include: - project:`).
- [x] Wrap the three internal Datadog GitHub Actions
  (`dd-octo-sts-action`, `commit-headless`, `dd-sts-action`) as composite
  actions under `.github/actions/` so the upstream SHA is centralized in one
  file per action.
- [x] Add a blocking CI lint (`scripts/check-external-refs.py`,
  wired as `external-refs-lint` in `.gitlab-ci.yml`) that fails the build
  when a PR introduces a non-allowlisted external reference. All patterns
  are matched case-insensitively so that `datadog/`, `DATADOG/`, etc. cannot
  evade the check. Patterns enforced:
  - `project: DataDog/` (cross-project GitLab triggers)
  - `--scope DataDog/` (dd-octo-sts scopes)
  - `gitlab.ddbuild.io/DataDog/` (internal GitLab URL rewrites)
  - `registry.ddbuild.io/` (internal container registry images)
  - `uses: DataDog/` (GitHub Actions in workflow files)
  - `repository: DataDog/` (actions/checkout repository targets)
  - `git clone …github.com/DataDog/` (Datadog-org git clones)
- [x] Add a positive assertion for wrapper composite actions under
  `.github/actions/<name>/action.yml`: every `uses:` reference MUST be
  SHA-pinned (40 hex chars). A tag/branch pin like `@v1.0.4` or `@main`
  slipped into a wrapper fails the `wrapper-uses-not-sha-pinned` check.
  This replaces the earlier whole-file (`:*`) allowlist for wrappers so
  that future regressions inside a wrapper are still caught.
- [ ] Document the migration runbook in `docs/contributing-release.rst` once
  available.
- [ ] Decide whether to fully vendor the upstream action sources for
  `dd-octo-sts-action`, `commit-headless`, `dd-sts-action` (currently the
  wrappers still delegate to `Datadog/<action>@<sha>`). Required before the
  upstream repos move to a private `ddoghq/` org if that happens.

### Phase 2 — Repo-path migration (only if our paths change)

- Update Chainguard STS subject patterns (5 files in `.github/chainguard/`).
- Update `dd-octo-sts --scope` arguments (~5 places).
- Update `gh run download --repo` and `target:` push references.
- Update `DD_GIT_REPOSITORY_URL`.
- Update `tests/sourcecode/test_source_code_link.py` allowlist.
- Update doc URLs.

### Phase 3 — External-action / external-repo migration

- For each Datadog-org action or repo confirmed to move to `ddoghq/`:
  - **Preferred:** request that the maintainers keep a public `Datadog/`
    mirror (so all OSS dd-trace-* repos can `uses:` it).
  - **Fallback:** vendor the action source under `.github/actions/`, or
    fork to a `Datadog/`-owned mirror we control.
- Update GitLab `trigger: project:` references.
- Update internal-GitLab clone URLs and the `insteadOf` rewrite.

