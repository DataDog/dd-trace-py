# Kafka throughput benchmark (DSM overhead)

Standalone Kafka produce/consume workload used to measure Data Streams
Monitoring (DSM) overhead on top of APM for the Python tracer. It is the Python
counterpart of the .NET `Samples.KafkaBenchmark` and is driven by the
`dd-trace-py/data-streams-monitoring` branch of the `benchmarking-platform`
repo.

## Files

- `kafka_throughput.py` — the workload: `NUM_THREADS` (default 5) parallel
  workers, each producing 1000 messages (5 headers each) to its own topic,
  flushing, then synchronously consuming and committing all 1000 back. No
  tracer-specific code — DSM is toggled purely via environment.
- `run_timeit.py` — timing harness (5 warmup + 25 timed iterations by default),
  emits a JSON artifact compatible with `steps/compare-results.py`. Reports
  median duration (ms) and median process RSS (bytes).
- `requirements.txt` — `confluent-kafka`, `psutil`.

## Running locally

Requires a Kafka broker on `localhost:9092`.

```bash
pip install -r requirements.txt

# DSM disabled (APM-only baseline)
DD_TRACE_ENABLED=true DD_DATA_STREAMS_ENABLED=false \
  KAFKA_TOPIC=test-topic-no-dsm TIMEIT_OUTPUT=tracer-no-dsm.json \
  ddtrace-run python run_timeit.py

# DSM enabled
DD_TRACE_ENABLED=true DD_DATA_STREAMS_ENABLED=true \
  KAFKA_TOPIC=test-topic-dsm TIMEIT_OUTPUT=dsm-enabled.json \
  ddtrace-run python run_timeit.py
```

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `NUM_THREADS` | `5` | Parallel produce/consume workers |
| `KAFKA_TOPIC` | `benchmark-topic` | Base topic name (per-run/per-worker suffixes appended) |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Broker address |
| `WARMUP` | `5` | Discarded warmup iterations |
| `COUNT` | `25` | Timed iterations feeding the median |
| `TIMEIT_OUTPUT` | `results.json` | Output artifact path |
| `DD_DATA_STREAMS_ENABLED` | — | The only differentiator between the two experiments |
