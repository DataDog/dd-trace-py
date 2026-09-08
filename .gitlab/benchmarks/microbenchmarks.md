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

**`microbenchmarks.yml`** — The child pipeline template — job definitions, image pins, and the
`benchmarking-platform` commit pin. Its `microbenchmarks` job has an intentionally empty
`parallel:matrix`.

**`bp-runner.yml`** — `bp-runner` experiment used only by scenarios named `flask_*` or `django_*`,
which need a live Datadog agent alongside the benchmark. Everything else goes straight to the
platform's `run-benchmarks.sh`.

**`bp-runner.microbenchmarks.fail-on-breach.template.yml`** — The SLOs the `check-slo-breaches` gate
checks results against: one threshold per scenario config. Scenario names here are
`<lowercased scenario class name>-<config name>` — the `start-finish` config of the `Span` class in
`benchmarks/span/` is `span-start-finish`.

Execution, once per pipeline:

1. `tests-gen` matches your changed files against `suitespec.yml`, appends a `parallel:matrix`
   entry per group of at most two scenarios to a copy of `microbenchmarks.yml`, and filters the
   SLO template down to the matching scenarios. Nothing relevant changed means a
   `microbenchmark-noop` job and no benchmarks.
2. `baseline:detect` resolves the comparison point, `baseline:build` produces its wheel (PyPI,
   then S3, then a source build), and `candidate` takes the `cp39` wheel from the parent
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

4. Add an SLO per config to `bp-runner.microbenchmarks.fail-on-breach.template.yml` so
   `check-slo-breaches` gates on it, keyed `<lowercased class name>-<config name>`:

   ```yaml
   - name: <classname>-<config>
     thresholds:
       - execution_time < 0.05 ms
   ```

   Base the number on your local run with a small margin above it (roughly 10%), not on the
   measurement exactly. A scenario with no entry here runs and reports but is not gated.

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

> [!NOTE]
> `FLAKY_BENCHMARKS_REGEX` is not declared in `microbenchmarks.yml` yet, and the gate does not
> read it yet either — the `check-slo-breaches` job currently runs `bp-runner` against the SLO
> file and nothing consults a flaky list. Adding the empty variable declaration, and the gate
> support behind it, is tracked separately.
>
> This is nonetheless the intended mechanism, so record the name here when the declaration lands
> rather than reaching for something else. In particular, do not work around it by deleting the
> scenario's thresholds from the SLO template: that drops the benchmark out of reporting as well as
> out of gating, so nobody sees the trend either.

Marking a benchmark flaky is a stopgap, not a resolution: it means nothing is watching that code
path for regressions. Open an issue to either stabilize the scenario or remove it.

## Rebuilding the CI Docker image

`MICROBENCHMARKS_CI_IMAGE` (`ci/benchmarking-platform:dd-trace-py`) is **not** built from this
repository. Its Dockerfile lives on the `dd-trace-py` branch of
[DataDog/benchmarking-platform](https://github.com/DataDog/benchmarking-platform), along with the
step scripts the jobs call. Do not look for a `container/` directory here — there is none.

You need a rebuild when the image contents change: a new Python version, a new system package, or a
change to the `bp-runner` tooling. You do **not** need one to change a scenario, a threshold, or a
`suitespec.yml` entry — those are read from your checkout at run time.

1. Open a pull request against the `dd-trace-py` branch of `benchmarking-platform` with the
   Dockerfile or step change. Its CI builds and publishes the image.
2. Note the published image tag.
3. In `microbenchmarks.yml`, point `BENCHMARKING_COMMIT_SHA` at the merged commit. Bump
   `MICROBENCHMARKS_CI_IMAGE` too if the tag itself changed — the jobs both run *in* the image and
   clone the repo at `BENCHMARKING_COMMIT_SHA` for its steps, so leaving the two at different
   revisions is a real source of confusing failures.
4. Push and confirm a green `microbenchmarks` child pipeline before merging.

The macrobenchmark image (`dd-trace-py-macrobenchmarks`) and the `python/macrobenchmarks` branch
follow the same pattern from `macrobenchmarks.yml`.

## Testing the CI Docker image locally

Pulling `MICROBENCHMARKS_CI_IMAGE` requires credentials for the `486234852809` ECR registry, so
authenticate with whichever AWS credential helper you normally use for Datadog build accounts:

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 486234852809.dkr.ecr.us-east-1.amazonaws.com

docker pull 486234852809.dkr.ecr.us-east-1.amazonaws.com/ci/benchmarking-platform:dd-trace-py
```

Run a scenario the way CI does, with the repository mounted:

```bash
docker run --rm -it \
  -v "$(pwd)":/dd-trace-py -w /dd-trace-py \
  -e SCENARIO=<name> \
  486234852809.dkr.ecr.us-east-1.amazonaws.com/ci/benchmarking-platform:dd-trace-py bash
```

Inside the container, clone the platform steps and put them on `PATH` as the job does:

```bash
git clone --branch dd-trace-py https://github.com/DataDog/benchmarking-platform /platform
export PATH="$PATH:/platform/steps"
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
