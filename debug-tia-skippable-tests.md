# Debugging the TIA Skippable Tests API

A field guide for debugging why the Test Impact Analysis (TIA / ITR) skippable-tests
endpoint returns (or fails to return) skippable tests. Written from a real
investigation into `dd-trace-py` returning "no skippable tests".

> Line numbers drift. Anchor on function names and grep; treat any `file:line`
> here as a starting hint, not gospel.

---

## 1. What TIA does (30-second model)

A test is **skippable** when its recorded per-test code coverage does **not**
overlap the files changed between the covered commit and the commit under test.

For that to work, three things must exist for a repo/service:

1. **Per-test coverage** uploaded by the tracer to the `citestcov` track.
2. **Git metadata** (packfiles) uploaded to `gitdb` so the backend can diff commits.
3. A **skippable request** from the tracer at session start.

The tracer only requests skips when the **settings** endpoint tells it
`tests_skipping: true`. The skippable endpoint itself, however, does **not**
re-check that setting (see §6) — it always computes from coverage + git.

Data flow:

```
tracer ──citestcov (unresolved coverage)──▶ ci-test-coverage-processor ──resolved──▶ Event Platform
tracer ──git packfiles──▶ gitdb
tracer ──POST /api/v2/ci/tests/skippable──▶ rapid-ci-app ──queries gitdb + coverage──▶ skippable list
```

---

## 2. Where the code lives

Service: **`rapid-ci-app`** (Rapid Go HTTP), in `dd-source`.

Base path: `domains/ci-app/apps/apis/rapid-ci-app/internal/itrapihttp/`

| Concern | File | Symbol |
|---|---|---|
| Route + handler | `api.go` | `getSkippableTests` (handler), `ConfigureItrAPI` (routes) |
| Request/response structs | `api.go` | `skippableTestsParams`, `SkippableTest`, `SkippableSuite`, `Meta` |
| Org FF gate (404) | `api.go` | `checkItrEnabled` middleware |
| V1 compute (Event Platform) | `skippable_tests.go` | `computeSkippableTests` |
| V2 compute (Trino/retriever) | `skippable_tests.go` | `computeSkippableTestsV2` |
| Commit traversal (gitdb log) | `traverse.go` | gitdb `Log()` streaming |
| Coverage query + filter build | `get_coverage.go` | `getFilterFromParams` |
| Coverage → diff comparison | `test_skipping.go` | `processCoverages`, `canSkipTest` |
| V2 Trino query builder | `retriever_get_skippables.go` | SQL template + `ExecuteSql` |

Related services:
- **`ci-test-coverage-processor`** (dd-go, `ci-app/apps/ci-test-coverage-processor`) —
  resolves raw citestcov by looking up the test span in `citest`, then writes
  resolved coverage back. **ITR only uses resolved coverage.**
- **`test-optimization-api`** (dd-source) — serves the per-service settings
  endpoint (`/api/v2/ci/libraries/tests/services/setting`) that gates the tracer.

Confluence: rapid-ci-app (`2587590664`); Skippable tests V2/Trino (`6986367984`);
ITR (`2890765528`).

---

## 3. Endpoint + auth

- `POST /api/v2/ci/tests/skippable`
- Host (prod US1): `https://api.datadoghq.com`
- Auth: `DD-API-KEY: <key>` for the target org. The org is derived from the key.
- Content type: `application/json`; response is JSON:API (`application/vnd.api+json`).

Org-level gate: feature flag **`ci_app_itr_api`**. If off for the org, the
endpoint returns **404** (not an empty 200).

Useful debug headers:
| Header | Effect |
|---|---|
| `X-Datadog-ITR-Disable-Cache: 1` | Bypass the response cache, force recompute |
| `X-Debug-Fingerprint: <fp>` | Extra server logging for one test fingerprint |
| `X-Final-Time: <ms>` | Override the coverage query's final-time window (reproduce a past run) |
| `X-Datadog-ITR-Implementation: v1|v2` | Force compute path |

---

## 4. Request schema (`data.attributes`, type `test_params`)

| Field | Required | Notes |
|---|---|---|
| `repository_url` | **yes** | e.g. `https://github.com/DataDog/dd-trace-py`. Normalized to a lowercased `git.repository.id_v2`. |
| `sha` | **yes** | Commit under test. If not in gitdb → **404**. |
| `service` | effectively yes | ITR filters coverage on `@service`. Omit it and you match nothing → empty. |
| `test_level` | no | `"test"` (default) or `"suite"`. |
| `configurations` | no, but matters | Exact-match filter (see §6). |
| `suite`, `module` | no | Extra filters. |

`configurations` keys: `os.platform`, `os.version`, `os.architecture`,
`runtime.name`, `runtime.version`, `runtime.vendor`, plus `device.*`, `ui.*`,
`test.bundle`, `custom`. `os.version` **is required** by validation if you send
`configurations` at all.

> `env` is intentionally **ignored** by this endpoint. Don't rely on it.

Example body:

```json
{
  "data": {
    "type": "test_params",
    "attributes": {
      "service": "dd-trace-py",
      "repository_url": "https://github.com/DataDog/dd-trace-py",
      "sha": "7681cfe8cb59f2f6ce124e7f9c7331e055ed8c73",
      "test_level": "test",
      "configurations": {
        "os.platform": "Linux",
        "os.version": "6.8.0-aws",
        "os.architecture": "x86_64",
        "runtime.name": "CPython",
        "runtime.version": "3.13.13"
      }
    }
  }
}
```

---

## 5. Response schema (HTTP 200)

```jsonc
{
  "meta": {
    "correlation_id": "<16-byte hex>",     // request id; use to find the trace
    "coverage": { "<file>": "<base64 line bitmap>" }  // aggregated, may be empty
  },
  "data": [
    {
      "type": "test",                       // or "suite" for test_level=suite
      "id": "<test.fingerprint>",
      "attributes": {
        "name": "test_x[py3.13]",
        "suite": "test_native_logger.py",
        "parameters": "{...}",
        "configurations": { "custom": {"python":"3.13","riot_hash":"..."}, "test.bundle":"tests.tracer" },
        "_is_missing_line_code_coverage": true
      }
    }
  ]
}
```

`data: []` with HTTP 200 = "computed successfully, nothing skippable."

---

## 6. Why you get an empty list (ranked by how often it bites)

1. **Missing `service`.** The coverage query ANDs `@service:"<service>"`. No
   service param → filter never matches resolved coverage → `data: []`.
   On the `citestcov` track note the quirk: `@test.service` is **null**;
   resolved events carry `@service`. Filter/inspect by `@service`.

2. **Configuration mismatch (exact match).** `getFilterFromParams` builds
   `@os.platform:"..." @os.version:"..." @os.architecture:"..." @runtime.name:"..." @runtime.version:"..."`
   with strict equality (only `os.version` has optional normalization behind FF
   `ci_app_itr_os_version_normalization`). If coverage was recorded on `x86_64`
   and you ask for `aarch64`, or `os.version` differs by one character, you get 0.
   Always pull the **exact** stored config first (see §8).

3. **`sha` has no usable coverage on it or its ancestors in-window.** Coverage
   must exist for a commit in the traversal window and match the config. If
   coverage collection was toggled off for recent commits, requesting HEAD
   returns 0 while an older coverage-bearing commit returns thousands.

4. **`ci_app_itr_api` FF off** → 404 (org gate), not an empty 200.

5. **SHA not in gitdb** → 404.

6. **Commit opt-out.** Current commit message (or the parent of a GH Actions
   merge commit) contains `itr:noskip` → intentional empty.

7. **Diffs touch broadly-covered files.** If the diff between covered commit and
   requested SHA hits files every test covers, nothing is skippable (working as
   intended, not a bug).

8. **Timeouts / truncation.** V2 has a global budget (~30s) and query-size caps
   (`maxDiffFilesPerCommit` ~5000, `maxTotalFuseHashes` ~100k). Very large diffs
   drop oldest commits. TIA also ignores a `citestcov` event with >16,000 covered
   files.

### Metrics that tell you which branch fired
`itr_api.skippable_tests.no_commits`, `.no_diffs`, `.no_skippable_tests`,
`.request_timeout`, `itr_api.tracked_files.no_commit_passed`,
`.partial_result`. If only `no_skippable_tests` increments, the pipeline ran to
completion and simply found nothing (reasons 1–3, 7) rather than short-circuiting.

---

## 7. Calling the endpoint

Returns ~61k skippable tests for a coverage-bearing dd-trace-py commit:

```bash
curl -s -X POST "https://api.datadoghq.com/api/v2/ci/tests/skippable" \
  -H "DD-API-KEY: $DD_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Datadog-ITR-Disable-Cache: 1" \
  -d '{"data":{"type":"test_params","attributes":{
        "service":"dd-trace-py",
        "repository_url":"https://github.com/DataDog/dd-trace-py",
        "sha":"7681cfe8cb59f2f6ce124e7f9c7331e055ed8c73",
        "test_level":"test",
        "configurations":{"os.platform":"Linux","os.version":"6.8.0-aws",
          "os.architecture":"x86_64","runtime.name":"CPython","runtime.version":"3.13.13"}}}}' \
  | jq '{skippable:(.data|length), correlation_id:.meta.correlation_id, sample:(.data[0])}'
```

Real compute is slow (10–16s for a large repo); drop the cache header for a
cached/faster read. A `400` like `attribute "configurations.os.os.version" is
required` means you sent `configurations` without `os.version`.

---

## 8. Querying `citestcov` coverage with `retriever-cli`

`citestcov` is the per-test coverage track. Use `retriever-cli` (installed at
`~/go/bin/retriever-cli`) to check what coverage actually exists — this is how you
confirm §6 reasons 1–3.

Prod requires `--customer-auth=skip` (OBO is blocked in prod; staging doesn't
need it). Default org is 2; DC `us1.prod.dog`.

**Count coverage for a branch, grouped by commit:**
```bash
retriever-cli -d us1.prod.dog -o 2 -e trino --customer-auth=skip \
  --start '14d ago' --end now --timeout 4m --json \
  -q "SELECT c0 AS sha, COUNT(*) AS n
      FROM TABLE(eventplatform.system.track(
        TRACK => 'citestcov', QUERY => '@git.branch:my\/branch',
        COLUMNS => ARRAY['@git.commit.sha'], OUTPUT_TYPES => ARRAY['varchar'])) AS t(c0)
      GROUP BY c0 ORDER BY n DESC"
```

**Get the exact stored config tuples for a commit** (feed these back into the
skippable request verbatim):
```bash
retriever-cli -d us1.prod.dog -o 2 -e trino --customer-auth=skip \
  --start '3d ago' --end now --timeout 4m --json \
  -q "SELECT c0 AS os_platform, c1 AS os_version, c2 AS os_arch, c3 AS rt_name, c4 AS rt_version, COUNT(*) AS n
      FROM TABLE(eventplatform.system.track(
        TRACK => 'citestcov',
        QUERY => '@git.branch:my\/branch @git.commit.sha:<sha> @service:my-service',
        COLUMNS => ARRAY['@os.platform','@os.version','@os.architecture','@runtime.name','@runtime.version'],
        OUTPUT_TYPES => ARRAY['varchar','varchar','varchar','varchar','varchar'])) AS t(c0,c1,c2,c3,c4)
      GROUP BY c0,c1,c2,c3,c4 ORDER BY n DESC"
```

**Resolved vs unresolved:** resolved events carry `@service` + a fingerprint +
`@test.status`. ITR only uses resolved coverage. The raw `resolved` bool is not
indexed, so use "has `@service`" as the resolved proxy.

`retriever-cli` gotchas:
- Filter by `@git.branch` / `@git.repository.id_v2` / `@service` — **not**
  `@test.service` (null on this track).
- `@timestamp` / bare `timestamp` don't project on the table function. Derive
  recency by narrowing `--start`.
- `OUTPUT_TYPES` uses `varchar` / `bigint` (not `int64`). Escape `/` in branch
  names: `my\/branch`.
- `@`-attributes can't be bare SQL identifiers; alias positionally with `AS t(c0,...)`.

---

## 9. Cross-checks: settings, coverage pipeline, traces

**Per-service settings** (does the tracer even ask for skips?):
```
get_datadog_test_optimization_settings(service=..., env=..., repository_url=...)
```
Look for `test_impact_analysis_enabled` and `code_coverage_enabled`. If TIA is
off, the tracer won't request skips and won't collect coverage by default — even
though the endpoint itself would still compute if called directly. Settings can
differ per `env` (a service may have `env=prod` configured but 404 for
`env=none`/`env=ci`).

**Coverage processor health** (is coverage being resolved?):
```
search_datadog_logs(query='service:ci-test-coverage-processor @org_id:<ORG> status:error', from='now-2h')
```
Fleet-wide "Test/span not found" is normal background noise (coverage arriving
before the test span indexes, then retried). Only worry if it's concentrated on
your org/service and coverage never becomes resolved.

**The request trace** (what did one request actually do?):
- Find it: `search_datadog_spans(query='service:rapid-ci-app resource_name:"POST /api/v2/ci/tests/skippable"')`.
- Expand child spans: `gitdb` `libgitdb.repository.Log.Next` (commit traversal),
  `PerformEVPQuery` (coverage cardinality), `retriever.trino.*` (V2 skippable
  query). Many gitdb Log spans + a Trino span = it found commits and ran the join;
  a fast return with neither = early exit (no commits / no coverage).

---

## 10. dd-trace-py specifics (context from the investigation)

- ITR CI env is configured in `riotfile.py` → `_configure_ci_itr_env_for_instance`.
  Key vars: `DD_CIVISIBILITY_ITR_ENABLED`, `_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE`
  (forces per-test coverage upload regardless of backend settings),
  `_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING` (collect/measure but don't actually skip).
- Coverage was toggled on/off across the branch ("force coverage" ↔ "Revert force
  coverage"), so only some commits have coverage. Request a coverage-bearing commit.
- All observed coverage was `x86_64`, `os.version=6.8.0-aws`, CPython
  3.9/3.10/3.11/3.12/3.13/3.14. No `aarch64` coverage existed — an `aarch64`
  request will always return 0.

### The one-paragraph takeaway
If skippable comes back empty, in order: (1) include `service`; (2) pull the exact
stored config from `citestcov` and match it byte-for-byte; (3) request a commit
that actually has coverage on it or a close ancestor. Most "TIA is broken" reports
are one of these three, not a backend fault.
