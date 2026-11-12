# kafka_dsm

Kafka produce/consume workload used to measure how much Data Streams Monitoring (DSM) adds on top
of APM. `bench.py` has no tracer-specific code; `run.sh` selects the scenario.

Each iteration: create a fresh single-partition topic and consumer group; (untimed) build a
`confluent_kafka` Producer/Consumer, fetch metadata and join the group; (timed) produce 1000
unkeyed messages (`b"x" * 1024`, headers `key0..key4` -> `value0..value4`) + `flush()`; (timed)
consume them with a synchronous `commit(msg)` every `COMMIT_EVERY` messages and after the last
one; (untimed) `consumer.close()`, then sample process RSS. Headers are a dict (ddtrace's DSM hook
skips list/tuple on older ddtrace versions). Duration per iteration = produce + consume.
Group join, `flush()` and consume each fail after 60 s instead of hanging. After the last
iteration (also on failure) the run's topics and consumer groups are deleted, best effort.

## Run

```
./run.sh <baseline|apm-only|apm+dsm> [slug]
```

- `baseline`: plain `python`, tracer not loaded.
- `apm-only`: `ddtrace-run`, `DD_DATA_STREAMS_ENABLED=false`.
- `apm+dsm`: `ddtrace-run`, `DD_DATA_STREAMS_ENABLED=true`. File/topic slug: `apm-plus-dsm`.

The ddtrace used is always the editable install of the checkout containing this folder
(`[tool.uv.sources]` in `pyproject.toml`), so the script works from any worktree. The first run
builds the native extensions in-tree (15-30+ min cold); later runs are no-ops. The build is run
with `GIT_CONFIG_GLOBAL=/tmp/empty-gitconfig CARGO_NET_GIT_FETCH_WITH_CLI=true`.

Requires Kafka on `localhost:9092` and an endpoint that accepts the tracer's payloads, such as a
Datadog Agent or a mock agent (`DD_TRACE_AGENT_URL`, default `http://127.0.0.1:18127`). A dead
port adds connection-error overhead to the tracer scenarios.

## Environment

| Variable | Default | |
|---|---|---|
| `COMMIT_EVERY` | 100 | sync commit every N messages; integer >= 1 |
| `NUM_THREADS` | 1 | only 1 is implemented |
| `WARMUP` / `COUNT` | 5 / 25 | discarded / timed iterations (`WARMUP` >= 0, `COUNT` >= 1) |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | |
| `KAFKA_TOPIC` | `benchmark-topic-python-<scenario-slug>-<slug>` | base name; run id and iteration are appended |
| `OUT_DIR` | `out/` in this folder | default directory for the outputs below |
| `EXPERIMENT_NAME` | scenario slug | `name` in the result JSON |
| `TIMEIT_OUTPUT` | `$OUT_DIR/python-<slug>.<scenario-slug>.json` | result JSON (see below) |
| `PROFILE` | 0 | 1: cProfile only the timed produce+consume phases of the timed iterations |
| `PROFILE_OUTPUT` | `$OUT_DIR/python-<slug>.<scenario-slug>` | writes `<PROFILE_OUTPUT>.prof` and `.top.txt` |
| `PROFILE_TOP_N` | 40 | rows in the top lists |
| `DDTRACE_WHEEL` | unset | path to a prebuilt ddtrace wheel to install instead of building the checkout (CI) |

CI: `.gitlab/benchmarks/dsm-throughput.yml` runs this on benchmarking-platform branch
`python/data-streams-monitoring` with the pipeline's cp314 wheel (automatic on `main`, manual elsewhere).

Measure timings with `PROFILE=0`; profiling inflates them. cProfile sees only the main thread, so
ddtrace background threads (DSM flush, trace writer) are not in the profile.

## Output

`TIMEIT_OUTPUT` holds one result per scenario, over the timed iterations:

```
[{"median": <duration ms>, "metrics": {
  "process.internal_duration_ms.median": <ms>, "process.internal_duration_ms.std_err": <ms>,
  "process.rss_bytes.median": <bytes>, "phase.produce_ms.median": <ms>,
  "phase.consume_ms.median": <ms>, "config.commit_every": <int>},
  "name": <EXPERIMENT_NAME>, "warmUpCount": <WARMUP>, "count": <COUNT>,
  "data": [{"status": "Passed", "metrics": {
    "process.internal_duration_ms": <produce + consume ms>,
    "process.corrected_duration_ms": <whole iteration wall ms, setup/teardown included>,
    "runtime.dotnet.mem.committed": <RSS bytes>}}, ...one per timed iteration]}]
```

`data` feeds bp-analyzer's TimeItSharp converter (`steps/convert-to-common-format.sh`), which reads
only per-iteration values under TimeItSharp metric names and maps `runtime.dotnet.mem.committed` to RSS.

Per-iteration `produce_ms` / `consume_ms` / `rss_bytes` go to a sibling file: `TIMEIT_OUTPUT=<name>.json`
also writes `<name>.runs.json` in the same directory (not the canonical schema; consumers globbing
`*.json` must skip `*.runs.json`). Its `env` holds only an allowlist of non-secret `DD_*` settings
(`RECORDED_ENV` in `bench.py`). `process.rss_bytes.median` is the lower median of the samples.
RSS is the current RSS (`/proc/self/status` `VmRSS`; `ps` where `/proc` is missing, e.g. macOS).
