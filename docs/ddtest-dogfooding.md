# ddtest dogfooding in dd-trace-py CI — final picture

This document describes the end state we are building toward: running
dd-trace-py's test suites through ddtest instead of the legacy
`riot run <hash> -- --ddtrace` path, replacing gitlab-level parallelization
with ddtest's plan/run + file splitting.

It generalizes the standalone `internal` pilot (`.gitlab/ddtest.yml`,
commit `b4040cc7b5`) to all test suites, wired into the generated-config
pipeline (`gen_gitlab_config.py`).

ddtest itself stays **unchanged**. All riot knowledge lives in the Python
bridge `scripts/ddtest-riot.py` (already landed). This is "Option 1" from
the design discussion: no coupling between ddtest and riot.

## Principles

1. **ddtest is single-venv and never compiles ddtrace.**
   Each riot venv is one `ddtest plan` + N `ddtest run --ci-node`
   invocations. ddtest jobs live in the **child pipeline** and `needs:
   build_base_venvs`, so they restore the prebuilt venv (ddtrace already
   compiled by `build_base_venvs`) — they never run `riot generate` and
   never recompile native extensions. The ddtest binary is built once in a
   setup job and shared as an artifact. (An earlier pilot built its own venv
   inline in the parent pipeline and OOMKilled recompiling ddtrace.)
2. **Riot knowledge stays in the bridge.** `scripts/ddtest-riot.py`
   enumerates venvs (`hashes`) and activates them (`venv-env`). ddtest
   shells out to it; ddtest never imports or knows about riot.
3. **Selection is inherited from suitespec.** ddtest jobs are emitted by
   `gen_gitlab_config.py` next to the legacy riot jobs, so a deselected
   suite emits no ddtest jobs at all. ddtest plan never runs on deselected
   suites/venvs.
4. **Opt-in per suite.** A suite flips to ddtest via a suitespec key; the
   legacy riot path remains the default and coexists, so rollout is
   suite-by-suite and reversible.
5. **K = per-venv CI nodes, from `ddtest_nodes`.** A suite that should
   not split across nodes uses `ddtest_nodes: 1` → ddtest runs all that
   venv's files in one node. K=1 is always valid; there is no special
   "cannot be parallelized" case.

## What changes

### riotfile (per suite, already proven on `internal`)

Each suite that opts into ddtest declares its test location and any
pytest options needed by ddtest as queryable env vars. Keeping these separate
from the legacy command avoids parsing shell command strings. A suite
using a pytest node ID keeps it in `DDTEST_SUITE_PATH` for legacy riot and
provides an equivalent file-level filter through `DDTEST_PYTEST_ADDOPTS`.

```python
Venv(
    name="internal",
    env={
        "DDTEST_SUITE_PATH": "tests/internal",
        "DDTEST_PYTEST_ADDOPTS": "-v",
        ...,
    },
    command="pytest -v -n auto --dist=worksteal {cmdargs} ${DDTEST_SUITE_PATH}/",
    ...
)
```

Legacy `riot run internal -- --ddtrace` is unchanged with or without
ddtest; the bridge reads `DDTEST_SUITE_PATH` to scope discovery per venv.

### suitespec (opt-in key)

A suite opts into the ddtest path with `ddtest: true`:

```yaml
suites:
  internal:
    ddtest: true          # <-- opt into ddtest plan/run
    venvs_per_job: 2
    snapshot: true
    paths: [...]
```

`DDTEST_SUITE_PATH` is required for every venv matched by a ddtest suite;
the generator rejects incomplete suite definitions. `ddtest_nodes` controls
`--min-parallelism` and `--max-parallelism`; legacy `parallelism` and
`venvs_per_job` are not used by ddtest.

### gen_gitlab_config.py (new emission)

`_gen_tests` emits, for each required suite with `ddtest: true`, one
**ddtest-plan** job per suite and **ddtest-run** jobs per `(venv, node)`,
mirroring the existing `JobSpec` emission but extending ddtest templates:

```
ddtest-build:                         stage: setup
  extends: .ddtest_build

<suite>/<name>::ddtest-plan          stage: <suite stage>
  extends: .ddtest_plan
  needs: [prechecks, build_base_venvs, ddtest-build]
  variables:
    RIOT_HASHES: "<hashes for this suite>"
    DDTEST_NODES: "<K>"
  artifacts:
    paths: [.testoptimization-*/]

<suite>/<name>::ddtest-run:<hash>:<node>  stage: <suite stage>
  extends: .ddtest_run
  needs: [prechecks, build_base_venvs, ddtest-build,
          <suite>/<name>::ddtest-plan]
  variables:
    RIOT_HASH: "<hash>"
    CI_NODE_INDEX: "<node>"
```

The plan job loops over hashes in isolated subshells, preparing and activating
one venv at a time. It uploads one partitioned plan artifact; run jobs restore
their matching partition. The binary is built once by `ddtest-build`, while
`build_base_venvs` supplies the precompiled ddtrace environments.

### .gitlab/tests.yml (new template)

A set of ddtest templates analogous to `.test_base_riot`: the setup
template builds ddtest once, while plan/run templates restore the ddtest
artifact, use the prebuilt riot environments, and set up the declared
service dependencies (snapshot suites get the testagent, same as
`.test_base_riot_snapshot`). They do **not** run riot's baked `command=`;
ddtest owns pytest invocation.

### scripts/ddtest-riot.py (already landed, unchanged)

- `hashes <pattern>` → `<hash>\t<py_hint>\t<DDTEST_SUITE_PATH>`
- `venv-env <hash>` → activation env (VIRTUAL_ENV, PATH, PYTHONPATH,
  RIOT_*, DDTEST_SUITE_PATH, DDTEST_PYTEST_ADDOPTS)

## CI graph (end state)

```
tests-gen (stage: tests)
  └─ gen_gitlab_config.py  ──► suitespec selection
                              emits riot jobs AND ddtest jobs for
                              required suites with ddtest: true

child pipeline (.gitlab/tests-gen.yml):
  build_base_venvs (per PYTHON_VERSION)
  ddtest-build                     (once when ddtest is selected)
  <suite>::riot:<hash>            (legacy, for non-ddtest suites)
  <suite>::ddtest-plan             (ddtest suites; loops over hashes)
  <suite>::ddtest-run:<hash>:<n>  (needs ddtest-plan:<hash>)
```

ddtest plan is downstream of suitespec generation (it lives in the child
pipeline that only contains selected suites), so deselected suites
emit no ddtest jobs — confirming the requirement that ddtest plan does
not run on deselected suites/venvs.

## Rollout

1. **Pilot (landed, corrected):** `ddtest-internal-pilot` job in the
   **child pipeline** (included from `.gitlab/tests.yml`, not the parent
   `.gitlab-ci.yml`), so it `needs: build_base_venvs` and reuses the
   prebuilt venv — no `riot generate`, no native compile. Auto-runs on push,
   `allow_failure: true`. Proves the bridge + ddtest plan/run +
   pytest-in-venv end-to-end. (An earlier version built its own venv inline
   in the parent pipeline and OOMKilled; the fix is to live in the child
   pipeline and reuse `build_base_venvs`.)
2. **gen_gitlab_config.py integration (this change):** `internal` sets
   `ddtest: true` in suitespec, emit ddtest-plan/run jobs via the
   generator. Pilot job removed (together with its `tests.yml` include,
   in the same commit — the missing-include lesson); `internal` runs
   through the generated path.
3. **Per-suite flip:** as each suite's ddtest path is validated, set
   `ddtest: true` on it. Legacy riot jobs for a suite are gone the moment
   it opts into ddtest.
4. **Default:** eventually `ddtest: true` becomes the default and the
   legacy riot emission is removed.

## What ddtest does not change

- ddtest source (Go) is untouched.
- The bridge is pure Python using riot as a library; no ddtest dependency.
- Local `riot run` keeps working without ddtest installed.
- Snapshot suites parallelize the same way they do today (per-job
  testagent + test_session_token); ddtest's cross-node file split is
  safe because each CI node has its own testagent service.
