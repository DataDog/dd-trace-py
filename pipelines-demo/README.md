# pipelines-demo — automatic DSM pipelines from APM services (DSM2-531)

Local end-to-end demo for the **Python + Kafka** slice of DSM2-531 (branch
`labbati/pipelines-from-apm`): run small Kafka apps with **DSM off**
(`DD_DATA_STREAMS_ENABLED` unset) and
watch the tracer's `DdPipelinesReportV1` sides/links flow to the tapped agent,
through the dd-source `dsm-auto-pipelines` harness, and out of the local
`dsm-api` as auto pipelines.

The exact Kafka topologies, expected per-process reports, and expected
`include_auto=true` output are in [../PIPELINE_TOPOLOGIES.md](../PIPELINE_TOPOLOGIES.md).
The other queuing systems (SQS, SNS, Kinesis, EventBridge, Pub/Sub, RabbitMQ)
and other tracer languages are separate subtasks of DSM2-531 with their own
demo topologies.
The cross-repo contract is `/home/bits/DSM/CONTRACT.md` (repo-external).

## Layout

```
pipelines-demo/
├── apps/            one small app per service; run under ddtrace-run
├── setup/           setup scripts (create the Kafka topics)
├── tap.py           agent stand-in on :8126; saves /v0.1/pipeline_stats payloads
├── tap/             (created at runtime) one .bin file per tapped payload
├── docker-compose.yml  kafka (+ zookeeper)
├── common.py        shared endpoint/env helpers
└── requirements.txt client libraries for the apps
```

## Prerequisites

- Docker (compose v2) for the brokers.
- Python 3 with this worktree's `ddtrace` importable (it is, from the worktree
  root — the demo runs every app via `python3 -m ddtrace.commands.ddtrace_run`).
- Client libraries: `pip3 install -r pipelines-demo/requirements.txt`
  (or just the ones for the topology you are running).
- The dd-source worktrees on `labbati/pipelines-from-apm`: the
  `dsm-auto-pipelines` service (harness) and `dsm-api`.

## End-to-end run order (topology 1: Kafka source-pipe-sink)

All commands run from the **worktree root** (the repo checkout), unless noted.
`pipelines-demo` below means this directory.

1. **Brokers up** (from `pipelines-demo/`):

   ```bash
   cd pipelines-demo && docker compose up -d && cd ..
   ```

2. **Tap up** (agent stand-in; leave it running in its own terminal):

   ```bash
   python3 pipelines-demo/tap.py
   ```

   Payloads appear as `pipelines-demo/tap/<unix_ts_ns>.bin` (raw gzip+msgpack
   `/pipeline_stats` bodies, one file per POST).

3. **Create the resources** (plain `python3`, NOT ddtrace-run, so the setup
   itself records no sides):

   ```bash
   python3 pipelines-demo/setup/setup_kafka.py
   ```

4. **Run the apps** with DSM off and the tap as agent. The common env:

   ```bash
   export DD_ENV=demo
   export DD_TRACE_AGENT_URL=http://localhost:8126
   export DD_DATA_STREAMS_PIPELINES_INTERVAL=5    # short window for the demo
   unset DD_DATA_STREAMS_ENABLED                  # DSM OFF — the whole point
   ```

   Then the three services, in order (src produces 3 messages, the pipe
   consumes and re-produces them, the sink drains them):

   ```bash
   DD_SERVICE=kafka-src python3 -m ddtrace.commands.ddtrace_run python pipelines-demo/apps/kafka_src.py
   DD_SERVICE=kafka-pipe python3 -m ddtrace.commands.ddtrace_run python pipelines-demo/apps/kafka_pipe.py
   DD_SERVICE=kafka-sink python3 -m ddtrace.commands.ddtrace_run python pipelines-demo/apps/kafka_sink.py
   ```

   Each app exits after its messages; the report window is also flushed by the
   tracer's atexit hook on exit. Watch the tap directory fill up:

   ```bash
   ls pipelines-demo/tap/
   ```

5. **Ingest the tapped payloads** with the dd-source harness (from the
   `dsm-auto-pipelines` worktree, see its own README/harness docs — pass each
   tapped file explicitly via `-payloads`; the harness extracts
   `DdPipelinesReportV1` and runs the same ingest path as the Kafka
   consumer):

   ```bash
   <dsm-auto-pipelines worktree>/harness -org 1 -payloads "$(ls pipelines-demo/tap/*.bin | paste -sd, -)"
   ```

6. **Query the local dsm-api**:

   ```bash
   curl 'http://localhost:8081/api/ui/data_streams/saved_pipelines?include_auto=true'
   ```

   (host/port per your local dsm-api). Expected auto pipelines for topology 1
   (full expectation, including the exact sides/links per process, in
   [../PIPELINE_TOPOLOGIES.md](../PIPELINE_TOPOLOGIES.md)):

   ```json
   {
     "pipelines": [
       {
         "id": "<sha1 hex: org|env|kafka-src:service|topic-a:topic|...>",
         "origin": "auto",
         "name": "kafka-src → kafka-sink",
         "env": "demo",
         "first_seen": 1766000000,
         "last_seen": 1766000000,
         "nodes": [
           {"type": "service", "name": "kafka-src"},
           {"type": "topic", "name": "topic-a", "queue_type": "kafka", "cluster": "localhost:9092"},
           {"type": "service", "name": "kafka-pipe"},
           {"type": "topic", "name": "topic-b", "queue_type": "kafka", "cluster": "localhost:9092"},
           {"type": "service", "name": "kafka-sink"}
         ]
       }
     ]
   }
   ```

   The wrapper keys follow dsm-api's existing saved-pipeline response; the
   auto entries are mapped at read time per CONTRACT.md section 4
   (`origin: "auto"`, `name = "<first service> → <last service>"`, U+2192).

## Other topologies

Every topology in [../PIPELINE_TOPOLOGIES.md](../PIPELINE_TOPOLOGIES.md) uses
the same flow: `docker compose up -d`, tap up, run the per-system setup script,
then run the apps with the common env above. Quick reference (all under
`python3 -m ddtrace.commands.ddtrace_run python pipelines-demo/apps/...`):

| Topology | Setup script(s) | Apps (one run per service, env in the topology doc) |
|---|---|---|
| 1. Kafka source-pipe-sink | `setup_kafka.py` | `kafka_src.py`, `kafka_pipe.py`, `kafka_sink.py` |
| 2. Kafka star, one group | `setup_kafka.py` | `kafka_src.py` (DD_SERVICE=star-src), `kafka_sink.py` ×3 (DD_SERVICE=star-c1/c2/c3, GROUP=star-group, TOPIC=topic-a) |
| 3. Kafka star, own groups | `setup_kafka.py` | same, GROUP=star-c1-group/… |
| 4. Kafka fan-out | `setup_kafka.py` | `kafka_src.py` (DD_SERVICE=fanout-src), `kafka_pipe.py` ×2 (TOPIC_OUT=topic-b/topic-c), `kafka_sink.py` ×2 (TOPIC=topic-b/topic-c) |

## Notes

- Every app is a plain script; messaging behavior (topics/queues/groups,
  message counts) comes from env vars with sensible defaults — see each app's
  docstring and the topology doc.
- `DD_TRACE_AGENT_URL` also covers the DSM agent URL in the Python tracer
  (there is no separate DSM URL setting), so the tap receives both classic
  trace traffic (ignored) and the `DdPipelinesReportV1` payloads.
- The setup scripts must never run under ddtrace-run: they are infrastructure,
  not demo services, and would otherwise emit their own sides.
- To re-run a topology from scratch: `rm -rf pipelines-demo/tap/` (and, for
  Kafka, use fresh consumer group names or reset offsets, since the topics
  keep their messages between runs).
