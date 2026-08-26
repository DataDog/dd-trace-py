# Benchmarks in CI

This directory holds the GitLab CI configuration that runs `ddtrace` benchmarks on the
[Benchmarking Platform](https://benchmarking.us1.prod.dog) and the performance quality gates
that block a pull request or a release when results regress.

This document is about **what runs in CI and what to do when it fails**. For running a benchmark
on your own machine — `scripts/run-benchmarks`, `scripts/perf-analyze`, and the scenario
framework — see [benchmarks/README.rst](../../benchmarks/README.rst).

Per-suite documentation:

* [microbenchmarks.md](microbenchmarks.md) — adding a benchmark, marking one known flaky,
  and rebuilding the CI image.

## Layout

**`microbenchmarks.yml`** — Child pipeline for the microbenchmark suite: builds a baseline wheel,
builds a candidate wheel, runs the scenarios under `benchmarks/` on bare metal, then gates on the
results. Runs on every pipeline, but only for the scenarios whose paths your commit touched. It is
a *template*: the `microbenchmarks` job's `parallel:matrix` is empty here and is appended by
`scripts/gen_gitlab_config.py` (see [How it fits together](#how-it-fits-together)).

**`macrobenchmarks.yml`** — Child pipeline for the macrobenchmark suite: runs Flask and Django
applications under load with various product configurations (tracing, appsec, IAST, profiling) and
measures request latency, throughput, CPU, and RSS. Runs automatically on `main`, on release
branches and tags, and on nightly builds; on a regular pull request branch it is a manual job.

**`serverless.yml`** — Included directly into the root `.gitlab-ci.yml` (not a child pipeline).
Triggers the `benchmark-serverless` job in `DataDog/serverless-tools`, which benchmarks the Lambda
layer built earlier in the pipeline. Runs whenever the `upload serverless` job has run.

**`bp-runner.yml`** — `bp-runner` experiment definition used by the microbenchmark suite for
scenarios that need a live Datadog agent — currently those whose names start with `flask_` or
`django_`. It pins the agent to CPUs 24-25 and the benchmark to CPUs 26-47 so the two never share a
core. All other scenarios bypass this file and run through the platform's `run-benchmarks.sh`
directly.

**`bp-runner.microbenchmarks.fail-on-breach.template.yml`** — SLO thresholds for every
microbenchmark scenario, currently one `execution_time` limit per scenario config. This is the file
you edit. It is **not** read by CI directly: `tests-gen` filters it down to the scenarios actually
running in this pipeline and writes `bp-runner.microbenchmarks.fail-on-breach.yml`, which is what
the gate reads. That generated file is a build artifact and is not committed.

**`bp-runner.macrobenchmarks.fail-on-breach.yml`** — SLO thresholds for the macrobenchmark
scenarios — latency percentiles, throughput, CPU, and RSS per application configuration. Read as-is
by the macrobenchmark gate, with no generation step. `warning_range: 7` means a measurement landing
within 7% of a threshold notifies Slack without failing the job.

**`steps/`** — Shell steps called from the microbenchmark jobs.

* `detect-baseline.sh` — decides which commit to compare against and writes `baseline.env`.
  On `main` and on release branches or tags it picks the latest non-rc tag; on a pull request
  branch it picks the merge base with the base branch.
* `build-baseline.sh` — obtains the baseline wheel: from PyPI when the baseline is a released
  tag, otherwise from the `dd-trace-py-builds` S3 bucket by commit SHA, and only failing that
  builds it from source.
* `combine-results.sh` — merges the per-config `results.*.json` files a scenario produces
  into one `results.json`, keeping a single copy of the shared metadata.

## External

Some of what runs is not visible from this directory.

**Benchmarking Platform steps.** The scripts that actually run and report a benchmark —
`run-benchmarks.sh`, `analyze-results.sh`, `capture-hardware-software-info.sh`,
`upload-results-to-s3.sh`, `upload-results-to-benchmarking-api.sh`, `post-pr-comment.sh` —
live in [DataDog/benchmarking-platform](https://github.com/DataDog/benchmarking-platform), not
here. The microbenchmark jobs clone that repo at the pin in `microbenchmarks.yml`
(`BENCHMARKING_BRANCH`, `BENCHMARKING_COMMIT_SHA`) and put `/platform/steps` on `PATH`. The
macrobenchmark jobs clone its `python/macrobenchmarks` branch. Changing how a benchmark is
executed or reported usually means a pull request there, followed by bumping the pin here.

**Gate job template.** `.check-slo-breaches` and `.notify-slo-breaches` come from
[DataDog/benchmarking-platform-tools](https://github.com/DataDog/benchmarking-platform-tools)
via `include:project:`. Both pipelines currently override the template's image with a pinned
`benchmarking-platform-tools-ubuntu` tag; the override is temporary and marked as such in the
YAML.

**Competitor benchmarks.** The root `.gitlab-ci.yml` triggers
[DataDog/apm-reliability/apm-sdks-benchmarks](https://gitlab.ddbuild.io/DataDog/apm-reliability/apm-sdks-benchmarks)
separately from anything in this directory. Its scenarios and thresholds are maintained there.

**Generation.** `scripts/gen_gitlab_config.py` produces `microbenchmarks-gen.yml` and the
filtered SLO file. Neither is committed; both are `tests-gen` artifacts.

## How it fits together

For microbenchmarks, on every pipeline:

1. **`tests-gen`** (root pipeline) runs `scripts/gen_gitlab_config.py`. It reads
   `benchmarks/suitespec.yml`, keeps the suites whose declared `paths` match the files your
   commit changed, and copies `microbenchmarks.yml` to `microbenchmarks-gen.yml` with a
   `parallel:matrix` entry appended per group of scenarios — at most
   `MAX_BENCHMARKS_PER_GROUP` (2) scenarios per job, grouped by `cpus_per_run`, to keep each
   job under about ten minutes. It also filters the SLO template down to the same set of
   scenarios. When nothing benchmark-relevant changed it emits a single `microbenchmark-noop`
   job and the rest of this list does not happen.
2. **`microbenchmarks`** (root pipeline) triggers `microbenchmarks-gen.yml` as a child
   pipeline, after `tests-gen` and the `build linux` job that produces the candidate wheel.
3. **`baseline:detect`** then **`baseline:build`** resolve and build the wheel to compare
   against, while **`candidate`** picks the `cp39` wheel out of the parent pipeline's
   artifacts. Both are cached, `baseline:build` on the baseline commit SHA.
4. **`microbenchmarks`** (child pipeline) runs each matrix entry's scenarios against both wheels,
   then `analyze-results.sh` and an S3 upload.
5. **`check-slo-breaches`** compares the results against the filtered SLO file and fails on a
   breach — this is the PR gate. It runs only if the benchmark job succeeded, because a benchmark
   failure already blocks the pull request on its own.
6. **`benchmarks-pr-comment`** uploads to the Benchmarking API and posts the results table as a
   pull request comment. It is `allow_failure: true` and never blocks.

For macrobenchmarks, the `macrobenchmarks` job triggers `macrobenchmarks.yml`, which builds a
candidate wheel, runs each application configuration, gates on
`bp-runner.macrobenchmarks.fail-on-breach.yml` in `check-slo-breaches`, and notifies
`#apm-python-release` from `notify-slo-breaches`. The gate here is `when: always`: it reports
even when a benchmark job failed, since a missing measurement is itself worth blocking a release
on.

### CI images

Each suite pins its own image, and the benchmark jobs run on dedicated bare-metal runner tags
(`runner:apm-k8s-tweaked-metal` for microbenchmarks, `runner:apm-k8s-same-cpu` for
macrobenchmarks) rather than shared Kubernetes runners, because run-to-run variance on shared
hardware is large enough to swamp the regressions these gates exist to catch.

| Suite | Image | Why |
|-------|-------|-----|
| Microbenchmarks | `MICROBENCHMARKS_CI_IMAGE` — `ci/benchmarking-platform:dd-trace-py` from the `486234852809` ECR registry | Built from the `dd-trace-py` branch of `benchmarking-platform` and carries the `bp-runner` tooling plus the Python versions the scenarios need. |
| Microbenchmark wheel builds | `PACKAGE_IMAGE` — `pypa/manylinux2014_x86_64` | `baseline:build` and `candidate` only need a manylinux toolchain, so they use the same image as the release wheel builds instead of the heavier benchmarking image. |
| Baseline detection | `GITHUB_CLI_IMAGE` — `dd-octo-sts-ci-base` | `baseline:detect` needs `gh` and `dd-octo-sts` to mint a GitHub token, and nothing else. |
| Macrobenchmarks | `MACROBENCHMARKS_CI_IMAGE` — `ci/benchmarking-platform:dd-trace-py-macrobenchmarks` | Separate image because it bundles the Flask and Django applications under test and their load-generation harness. |
| SLO gates | `benchmarking-platform-tools-ubuntu`, pinned by tag | Supplied by the `benchmarking-platform-tools` template; both pipelines pin a tag as a temporary override. |
| Serverless | `SLS_CI_IMAGE` — `ci/serverless-tools` | The suite runs entirely in `DataDog/serverless-tools`; this repo only triggers it. |

## Performance quality gates

Two gates block on performance, and they answer different questions.

|  | PR gate | Pre-release gate |
|--|---------|------------------|
| Job | `check-slo-breaches` in the `microbenchmarks` child pipeline | `check-slo-breaches` in the `macrobenchmarks` child pipeline |
| Thresholds | `bp-runner.microbenchmarks.fail-on-breach.template.yml` | `bp-runner.macrobenchmarks.fail-on-breach.yml` |
| Blocks | Merging the pull request | Pushing a release tag or branch |
| Catches | A single change making an operation obviously slower | Regression accumulated across many changes, each too small to trip the PR gate |

### Reading the gate output

`check-slo-breaches` logs one line per scenario and metric:

* 🟩 pass — within SLO.
* 🟥 breach — over SLO. Blocks.
* 🟨 warning — within `warning_range` percent of the SLO but not over it. Does not block;
  notifies Slack. Worth acting on, since it is the breach that blocks you next release. Only the
  macrobenchmark thresholds set `warning_range` today, so warnings come from that gate.
* `(unstable)` — variance too high to conclude anything. See
  [microbenchmarks.md](microbenchmarks.md) for what to do about a benchmark that is
  persistently unstable.

Each line carries a confidence interval, for example `[2.024ms; 2.042ms]`, rather than a single
average. A breach is only reported when the whole interval is on the wrong side of the threshold,
which keeps noisy scenarios from failing pipelines at random.

### Re-running after a fix

**Re-run both jobs, in order.** The gate scores artifacts from a previous benchmark run; it does
not measure anything itself. Re-running only the gate re-reads the old numbers and reaches the same
verdict, which is the usual reason a fix appears to have changed nothing.

For a microbenchmark breach, in the `microbenchmarks` child pipeline:

1. `microbenchmarks`
2. then `check-slo-breaches`

For a macrobenchmark breach, the same order in the `macrobenchmarks` child pipeline: the scenario
job named in the gate output — the job names there are the configuration names, such as
`tracing-rc-disabled-telemetry-disabled` or `appsec-enabled-iast-enabled-ep-disabled` — and then
`check-slo-breaches`. Note that the gate reports scenarios as
`<load profile>/<configuration>` (for example
`normal_operation/tracing-rc-disabled-telemetry-disabled`), so strip the load-profile prefix to
get the job name.

Pushing a new commit runs both in the right order, so this only matters when retrying without a
code change — for instance after a suspected infrastructure flake.

### When a gate is breached

Two options. Pick deliberately; do not bypass because the gate is in the way.

**Option 1 — fix the regression.** The right default when the regression is unintended.

1. Read the gate log to find which scenario and metric moved, and by how much.
2. Look at the scenario's history on the
   [APM SDKs Perf SLOs (with microbenchmarks) dashboard](https://app.datadoghq.com/dashboard/9yr-fpq-4v7)
   or, for macrobenchmarks, the
   [APM SDKs Perf SLOs dashboard](https://app.datadoghq.com/dashboard/w6a-xc3-kyz). A step change
   points at one commit; a slope points at accumulation. The
   [Benchmarking Platform trends](https://benchmarking.us1.prod.dog/trends) view gives the same
   data per commit.
3. Reproduce locally and find the cause with `scripts/run-benchmarks` and
   `scripts/perf-analyze --profile-compare`. See
   [benchmarks/README.rst](../../benchmarks/README.rst).
4. Push the fix and re-run both jobs as above.

**Option 2 — accept the regression and loosen the gate.** Appropriate when the cost is understood
and deliberate: a correctness or security fix that cannot be made cheaper, or a threshold that was
always too tight.

* For a **PR gate** breach, raise the breached threshold in
  `bp-runner.microbenchmarks.fail-on-breach.template.yml`, in the same pull request.
* For a **pre-release gate** breach, raise the breached threshold in
  `bp-runner.macrobenchmarks.fail-on-breach.yml`.

Both gates here compare measurements against fixed SLO thresholds, so editing the threshold is the
only bypass. The `performance/ignore-performance-regression` label described in the cross-language
user guide applies to repositories whose PR gate is a percentage-regression check
(`fail-on-regression`); dd-trace-py does not use one, and the label has no effect on
`check-slo-breaches`.

Either way, **comment on the pull request explaining why the regression is accepted.** A threshold
change without a rationale is indistinguishable from a threshold change to make CI quiet, and it
raises the bar permanently for everyone after you.

When setting a new threshold, put it a small margin (roughly 10%) above the current measurement
rather than exactly at it, or the next run's noise breaches it again.

## Further reading

* [Performance Quality Gates user guide](https://datadoghq.atlassian.net/wiki/spaces/APMINT/pages/5158175217/Performance+Quality+Gates)
  — the cross-language reference this section is adapted from.
* [How to set up pre-release performance quality gates](https://datadoghq.atlassian.net/wiki/spaces/APMINT/pages/5070193198/How+to+set+up+pre-release+performance+quality+gates)
  — including how to choose thresholds.
* [benchmarks/README.rst](../../benchmarks/README.rst) — running benchmarks locally.
