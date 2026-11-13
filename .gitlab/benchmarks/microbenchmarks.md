# Microbenchmarks in CI

The microbenchmark suite runs the scenarios under `benchmarks/` against two `ddtrace` wheels —
a baseline and a candidate (your commit) — on dedicated bare-metal runners, and gates the pull request on the
result based on performance SLOs. 

This document covers the CI side. For running the same scenarios locally, see
[benchmarks/README.rst](../../benchmarks/README.rst). For the pipeline as a whole and for what to
do about a breached gate, see [README.md](README.md).

## Setup

Four things define the suite:

**`benchmarks/suitespec.yml`** — Declares each scenario: which paths it is relevant to, and how many
CPUs one run needs. This is what decides whether your commit runs a given scenario at all.

**`microbenchmarks.yml`** — The child pipeline template — job definitions and image pins. Its
`microbenchmarks` job has an intentionally empty `parallel:matrix`.

**`bp-runner.yml`** — `bp-runner` experiment used only by scenarios named `flask_*` or `django_*`,
which need a live Datadog agent alongside the benchmark. Everything else goes straight to
`steps/run-benchmarks.sh`.

**`bp-runner.microbenchmarks.fail-on-breach.template.yml`** is no longer the source of truth;
the SLOs now live in one per-team file under **`.gitlab/benchmarks/slos/`** (see [SLO ownership](#slo-ownership)).
`scripts/gen_gitlab_config.py` merges those into the single generated
`bp-runner.microbenchmarks.fail-on-breach.yml` that the `check-slo-breaches` gate checks results
against: one threshold per scenario config. Scenario names are
`<lowercased scenario class name>-<config name>` — the `start-finish` config of the `Span` class in
`benchmarks/span/` is `span-start-finish`.

Execution, once per pipeline:

1. `tests-gen` matches your changed files against `suitespec.yml`, appends a `parallel:matrix`
   entry per group of at most two scenarios to a copy of `microbenchmarks.yml`, and filters the
   SLO template down to the matching scenarios. Nothing relevant changed means a
   `microbenchmark-noop` job and no benchmarks.
2. `baseline:detect` resolves the comparison point, `baseline:build` produces its wheel (PyPI,
   then S3, then a source build), and `candidate` takes the `cp312` wheel from the parent
   pipeline.
3. Each `microbenchmarks` matrix job runs its scenarios against both wheels and uploads results.
4. `check-slo-breaches` scores the results against the filtered SLO file;
   `benchmarks-pr-comment` posts the table on the pull request.

## Adding a benchmark

1. Write the scenario under `benchmarks/<name>/` — `scenario.py`, `config.yaml`, and
   `requirements_scenario.txt` if it needs extra dependencies. See the framework section of
   [benchmarks/README.rst](../../benchmarks/README.rst).

2. Run it locally and confirm it is stable before wiring it into CI:

   ```bash
   scripts/run-benchmarks --scenario <name> --artifacts ./benchmark-artifacts/
   scripts/perf-analyze benchmark-artifacts/
   ```

3. Declare it in `benchmarks/suitespec.yml`, alongside the existing entries:

   ```yaml
   <name>:
     paths:
       - '@bootstrap'
       - '@core'
       - '@tracing'
       - '@vendor'
       - benchmarks/<name>/*
       - benchmarks/suitespec.yml
     cpus_per_run: 1
     type: 'microbenchmark'
   ```

   `paths` decides when the scenario runs — list the component aliases and the source paths whose
   changes should trigger it, and be specific. A scenario listing broad paths runs on pipelines it
   cannot say anything about, which costs runner time and adds gate noise. Include
   `benchmarks/<name>/*` so changes to the scenario itself run it, and `benchmarks/suitespec.yml`
   so changes to the declaration do too.

   `type: 'microbenchmark'` is required; the generator selects on it. `cpus_per_run` groups
   scenarios into jobs — leave it at `1` unless the scenario genuinely needs more.

4. Add an SLO per config to your team's file under `.gitlab/benchmarks/slos/` so
   `check-slo-breaches` gates on it, keyed `<lowercased class name>-<config name>`:

   ```yaml
   - name: <classname>-<config>
     thresholds:
       - execution_time < 0.05 ms
   ```

   Base the number on your local run with a small margin above it (roughly 10%), not on the
   measurement exactly. A scenario with no entry runs and reports but is not gated.

   Each file under `slos/` is owned by a team via `.github/CODEOWNERS` (the file name is the
   team slug, e.g. `apm-sdk-capabilities.yml`), so editing a threshold routes review to
   that team automatically. If your team has no file yet, add one named `<team-slug>.yml`, add a
   matching CODEOWNERS rule, and push — `tests-gen` validates SLO integrity via gen_gitlab_config.py.

5. Verify the generated config before pushing:

   ```bash
   scripts/gen_gitlab_config.py --file benchmarks/<name>/scenario.py --verbose
   ```

   Check that `.gitlab/benchmarks/microbenchmarks-gen.yml` lists your scenario in a matrix entry
   and that `.gitlab/benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml` kept its
   thresholds. Both files are generated artifacts — do not commit them.

6. Run `scripts/lint suitespec-check`, then push. The first pipeline compares against a baseline
   that does not contain the scenario, so there is nothing to compare; from the next commit on it
   is gated normally.

## Marking a benchmark as known flaky

A benchmark whose run-to-run variance is large enough to breach its threshold at random wastes
everyone's time, and worse, teaches people to re-run gates until they pass. Take it out of the
blocking set — but confirm it is actually unstable first, because a benchmark that regressed
genuinely looks much the same from a single failed pipeline.

### Confirming instability

Measure variance with the same code on both sides. If baseline and candidate are identical and the
results still move, the benchmark is unstable; if they do not, you have a real regression.

The easiest way is the `generate-dd-trace-py` manual job in the
[`monitor-stability` pipeline](https://github.com/DataDog/benchmarking-platform-tools/blob/main/scripts/stability/.gitlab/monitor-stability.yml)
in `benchmarking-platform-tools`, run against the ref you want to check. It generates and runs benchmarks multiple times, then the `trigger-dd-trace-py` and `collect-dd-trace-py` jobs
that need it collect the samples from S3 and report run-to-run variance.

Also check whether the gate is already telling you: an `(unstable)` line in the
`check-slo-breaches` log means the platform itself found, within a single benchmark, the variance too high to judge.

### Recording it

Add the scenario name to `FLAKY_BENCHMARKS_REGEX` in `microbenchmarks.yml`. It is a
`|`-delimited regex matched against scenario names. A flagged benchmark still runs and still
reports its numbers, so trends stay visible; it just does not fail the pipeline.

Marking a benchmark flaky is a stopgap, not a resolution: it means nothing is watching that code
path for regressions. Open an issue to either stabilize the scenario or remove it.

## SLO ownership

SLO thresholds live in one per-team file under `.gitlab/benchmarks/slos/`, named
`<team-slug>.yml` (e.g. `apm-sdk-capabilities.yml`). Each file is owned by its team via
`.github/CODEOWNERS`, so editing a threshold routes review to that team automatically — GitHub
CODEOWNERS is file-level, so splitting the SLOs by team into separate files is what lets the gate
route per team. `scripts/gen_gitlab_config.py` merges all of these into the single generated
`bp-runner.microbenchmarks.fail-on-breach.yml` consumed by `check-slo-breaches`.

`gen_gitlab_config.py` also validates structural integrity every time it runs (i.e. on every PR
via the `tests-gen` job) and fails the build if an SLO gets orphaned — specifically if:

- an SLO points at a benchmark class or config that no longer exists,
- an SLO appears in more than one team file (two teams must not own the same gate), or
- a benchmark config has no SLO entry.

When you add, rename, or delete a benchmark scenario, config, or SLO threshold, push and fix
what `tests-gen` reports in the same PR.

## Rebuilding the CI Docker image

`MICROBENCHMARKS_CI_IMAGE` is built **from this repository**, by the manual
`build-benchmark-ci-images` job in the `microbenchmarks` child pipeline. The job comes from the
[`build-ci-images.template.yml`](https://github.com/DataDog/benchmarking-platform-tools/blob/main/images/templates/gitlab/build-ci-images.template.yml)
template and builds `.gitlab/benchmarks/container/`, pushing to
`registry.ddbuild.io/ci/benchmarking-platform:dd-trace-py-<pipeline id>`.

You need a rebuild when the image contents change: a new Python version, a new system package, or
a change to the `bp-runner` tooling. You do **not** need one to change a scenario, a threshold, or
a `suitespec.yml` entry — those are read from your checkout at run time.

1. Open a pull request here changing `.gitlab/benchmarks/container/` (the Dockerfile or
   `install-base-deps.sh`).
2. On the branch's pipeline, trigger the manual `build-benchmark-ci-images` job and wait for it
   to finish. Its log shows the tag it pushed:
   `registry.ddbuild.io/ci/benchmarking-platform:dd-trace-py-<pipeline id>`.
3. In `microbenchmarks.yml`, repin `MICROBENCHMARKS_CI_IMAGE` to that tag and push.
4. Confirm a green `microbenchmarks` child pipeline before merging.

The image's base layer (`INTERMEDIATE_CI_IMAGE` in the Dockerfile) is built from this
repository's own `benchmarks/Dockerfile` by the `dd-trace-py` branch pipeline of
[DataDog/benchmarking-platform](https://github.com/DataDog/benchmarking-platform) and published
as `ci/benchmarking-platform:cache-dd-trace-py`; the `FROM` pins it by digest. To pick up a
change to `benchmarks/Dockerfile`, run that branch's manual `build-ci-images` job, take the new
`cache-dd-trace-py` digest from its log, and update the `ARG INTERMEDIATE_CI_IMAGE` default.

The step scripts in `steps/` are not part of the image — the jobs run them from the checkout —
so changing one needs no rebuild.

The macrobenchmark image (`dd-trace-py-macrobenchmarks`) is still built from the
`python/macrobenchmarks` branch of `benchmarking-platform`; see `macrobenchmarks.yml`.

## Testing the CI Docker image locally

Pulling `MICROBENCHMARKS_CI_IMAGE` requires credentials for the `registry.ddbuild.io` registry, so
authenticate with whichever Datadog credential helper you normally use for it:

```bash
docker pull registry.ddbuild.io/ci/benchmarking-platform:dd-trace-py-<pipeline id>
```

Run a scenario the way CI does, with the repository mounted:

```bash
docker run --rm -it \
  -v "$(pwd)":/dd-trace-py -w /dd-trace-py \
  -e SCENARIO=<name> \
  registry.ddbuild.io/ci/benchmarking-platform:dd-trace-py-<pipeline id> bash
```

Inside the container, put the step scripts from the checkout on `PATH` as the job does:

```bash
export PATH="$PATH:/dd-trace-py/.gitlab/benchmarks/steps"
export REPORTS_DIR="$(pwd)/reports/<name>/" && mkdir -p "$REPORTS_DIR"
run-benchmarks.sh
```

Two things will not match CI, and both matter for numbers rather than for correctness: your machine
is not the pinned bare-metal runner, so absolute timings and variance differ; and CPU pinning
(`bp-runner.yml` reserves cores 24-47) assumes a core count you probably do not have. Use this to
check that a scenario *runs* in the image — that dependencies resolve and the scenario starts — and
use CI for numbers you intend to act on.

For a `flask_*` or `django_*` scenario, exercise the `bp-runner` path instead:

```bash
BP_SCENARIO=<name> bp-runner .gitlab/benchmarks/bp-runner.yml --debug -t
```
