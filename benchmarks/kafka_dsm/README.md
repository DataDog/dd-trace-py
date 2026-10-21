# kafka_dsm

Kafka produce/consume workload used to measure how much Data Streams Monitoring (DSM) adds on top
of APM. `bench.py` has no tracer-specific code; `run.sh` selects the scenario.

Each iteration: create a fresh single-partition topic; (untimed) build a `confluent_kafka`
Producer/Consumer and join the group; (timed) produce 1000 messages with 5 headers + `flush()`;
(timed) consume them with a synchronous `commit(msg)` every `COMMIT_EVERY` messages and after the
last one; (untimed) `consumer.close()`. Headers are a dict (ddtrace's DSM hook skips list/tuple).

## Run

```
./run.sh <baseline|apm-only|apm+dsm> [slug]
```

- `baseline`: plain `python`, tracer not loaded.
- `apm-only`: `ddtrace-run`, `DD_DATA_STREAMS_ENABLED=false`.
- `apm+dsm`: `ddtrace-run`, `DD_DATA_STREAMS_ENABLED=true`.

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
| `COMMIT_EVERY` | 100 | sync commit every N messages |
| `NUM_THREADS` | 1 | only 1 is implemented |
| `WARMUP` / `COUNT` | 5 / 25 | discarded / timed iterations |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | |
| `KAFKA_TOPIC` | `benchmark-topic-python-<scenario>-<slug>` (`+` -> `plus`) | base name; run id and iteration are appended |
| `TIMEIT_OUTPUT` | `out/python-<slug>.<scenario>.json` | result JSON (`runs[]` with `produce_ms`/`consume_ms`, `max_rss_mb`) |
| `PROFILE` | 0 | 1: cProfile only the timed produce+consume phases of the timed iterations |
| `PROFILE_OUTPUT` | `out/python-<slug>.<scenario>` | writes `<PROFILE_OUTPUT>.prof` and `.top.txt` |
| `PROFILE_TOP_N` | 40 | rows in the top lists |

Measure timings with `PROFILE=0`; profiling inflates them. cProfile sees only the main thread, so
ddtrace background threads (DSM flush, trace writer) are not in the profile.
