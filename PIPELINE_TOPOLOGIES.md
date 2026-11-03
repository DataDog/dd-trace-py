# Demo pipeline topologies — automatic DSM pipelines from APM services (DSM2-531)

This document pins, for the local Kafka demo in `pipelines-demo/`, the
participating services, the exact expected pipelines (ordered node lists), and
the expected per-process tracer state, so the end-to-end run can be verified
against the dd-source `dsm-auto-pipelines` harness and `dsm-api` output.

This is the **Python + Kafka** slice of DSM2-531: each (tracer language, queuing
system) pair is its own subtask with its own demo topologies; this document
covers the Kafka ones only.

The wire format, side/link encoding, and matching rules are pinned in the
cross-repo contract (`/home/bits/DSM/CONTRACT.md`, repo-external). This
document applies them to the demo resources (`pipelines-demo/setup/`).

## Notation

- **Sides** are the 5-tuples recorded in `DdPipelinesReportV1`:
  `(direction, queue_type, cluster, topic, group)` — direction `0` = produce,
  `1` = consume; `group` is `""` where the system has no consumer group.
- **Links** are `[in_side, out_side]` indices into that report's sides;
  `in_side = -1` means the produce is a **source** (no consumed-message
  context on the producing thread).
- **Pipelines** are ordered node lists with `type: service|topic`. Topic nodes
  carry `queue_type` and `cluster` where relevant. Topics match on
  `(queue_type, cluster, topic)`; a consumer with `cluster == ""` matches on
  `(queue_type, topic)` (contract §3, B-2).
- All demo apps run with **DSM OFF** (`DD_DATA_STREAMS_ENABLED` unset) and
  `DD_ENV=demo`; reports therefore come from the new
  `DD_DATA_STREAMS_PIPELINES_ENABLED` feature alone.
- Kafka in the demo is one broker reached at `localhost:9092`; all Kafka
  sides below use `cluster = "localhost:9092"` (bootstrap servers / cluster id
  when known — whatever the hook records is identical on both sides of every
  link because both come from the same broker, so the pipelines are unchanged
  even if the exact cluster value differs, including `""`).

Unless stated otherwise every app sends/consumes `MSGS=3` messages and exits,
so each process emits exactly one report window (plus the atexit flush).

## 1. Kafka source-pipe-sink

Services (`DD_SERVICE`): `kafka-src`, `kafka-pipe` (group `pipe-group`),
`kafka-sink` (group `sink-group`).

Run: `setup/setup_kafka.py`, then `apps/kafka_src.py` (TOPIC=topic-a) →
`apps/kafka_pipe.py` (TOPIC_IN=topic-a, TOPIC_OUT=topic-b) →
`apps/kafka_sink.py` (TOPIC=topic-b).

**Expected pipeline (one):**

```
kafka-src (service)
→ topic-a (topic, kafka, cluster=localhost:9092)
→ kafka-pipe (service)
→ topic-b (topic, kafka, cluster=localhost:9092)
→ kafka-sink (service)
```

Name in the API: `kafka-src → kafka-sink`.

## 2. Kafka star, single group

One source feeding `topic-a`; three consumers in **one** group `star-group`
consuming it: `star-src`, `star-c1`, `star-c2`, `star-c3`
(`apps/kafka_sink.py` run three times with `DD_SERVICE=star-c1|c2|c3`,
`TOPIC=topic-a`, `GROUP=star-group`).

**Expected pipelines (three, one per consumer service, all ending at the
consumer):**

```
star-src → topic-a (kafka) → star-c1
star-src → topic-a (kafka) → star-c2
star-src → topic-a (kafka) → star-c3
```

## 3. Kafka star, multi group

Same as topology 2 but each consumer is in **its own** group
(`GROUP=star-c1-group`, `star-c2-group`, `star-c3-group`).

**Expected pipelines (still three, identical node lists to topology 2):** the
group is part of the consume side key, not a different topic, so it does not
change the pipeline — only the side keys in the reports (section 7).

## 4. Kafka fan-out

One producer to `topic-a`; two consumers that each also produce to their own
topic read by their own sink: `fanout-src`, `fanout-pipe1`
(`TOPIC_OUT=topic-b`), `fanout-pipe2` (`TOPIC_OUT=topic-c`), `fanout-sink1`
(`TOPIC=topic-b`), `fanout-sink2` (`TOPIC=topic-c`).

**Expected pipelines (two, full pipelines through the pipe services):**

```
fanout-src → topic-a (kafka) → fanout-pipe1 → topic-b (kafka) → fanout-sink1
fanout-src → topic-a (kafka) → fanout-pipe2 → topic-c (kafka) → fanout-sink2
```

## 5. State: DSM OFF, expected reports per process, expected API output

Every process above runs with `DD_DATA_STREAMS_ENABLED` **unset** (DSM off —
the whole point of DSM2-531), `DD_ENV=demo`, `DD_DATA_STREAMS_PIPELINES_ENABLED`
left at its default (`true`), and `DD_TRACE_AGENT_URL=http://localhost:8126`
(the tap; the Python tracer uses this URL for the
`/v0.1/pipeline_stats` pipeline payloads too). Each process that touched
messaging emits one `DdPipelinesReportV1` per window (the demo uses
`DD_DATA_STREAMS_PIPELINES_INTERVAL=5` and the atexit flush).

Below, `k` abbreviates the Kafka cluster value `localhost:9092`. Sides are
`(direction, queue_type, cluster, topic, group)`; links are indices into that
report's own sides.

### 1. Kafka source-pipe-sink

| Process | sides | links |
|---|---|---|
| `kafka-src` | `[[0, "kafka", k, "topic-a", ""]]` | `[[-1, 0]]` |
| `kafka-pipe` | `[[1, "kafka", k, "topic-a", "pipe-group"], [0, "kafka", k, "topic-b", ""]]` | `[[0, 1]]` |
| `kafka-sink` | `[[1, "kafka", k, "topic-b", "sink-group"]]` | `[]` |

Expected `GET /api/ui/data_streams/saved_pipelines?include_auto=true` adds one
auto pipeline (mapped to the saved-pipeline response shape per contract §4):

```json
{
  "origin": "auto",
  "name": "kafka-src → kafka-sink",
  "env": "demo",
  "first_seen": "<unix seconds>",
  "last_seen": "<unix seconds>",
  "nodes": [
    {"type": "service", "name": "kafka-src"},
    {"type": "topic", "name": "topic-a", "queue_type": "kafka", "cluster": "localhost:9092"},
    {"type": "service", "name": "kafka-pipe"},
    {"type": "topic", "name": "topic-b", "queue_type": "kafka", "cluster": "localhost:9092"},
    {"type": "service", "name": "kafka-sink"}
  ]
}
```

### 2. Kafka star, single group

| Process | sides | links |
|---|---|---|
| `star-src` | `[[0, "kafka", k, "topic-a", ""]]` | `[[-1, 0]]` |
| `star-c1` | `[[1, "kafka", k, "topic-a", "star-group"]]` | `[]` |
| `star-c2` | `[[1, "kafka", k, "topic-a", "star-group"]]` | `[]` |
| `star-c3` | `[[1, "kafka", k, "topic-a", "star-group"]]` | `[]` |

Three consume sides (same key, different report `service`). Expected API
output: three auto pipelines `star-src → topic-a → star-c1|c2|c3`
(names `star-src → star-c1` etc.), one per consumer service, each ending at
the consumer.

### 3. Kafka star, multi group

Same as 2, but the consume sides differ per consumer by group —
`star-c1`: `(1, "kafka", k, "topic-a", "star-c1-group")`, and likewise
`star-c2-group`, `star-c3-group`. Expected API output: the same three
pipelines as topology 2 (group is not a node).

### 4. Kafka fan-out

| Process | sides | links |
|---|---|---|
| `fanout-src` | `[[0, "kafka", k, "topic-a", ""]]` | `[[-1, 0]]` |
| `fanout-pipe1` | `[[1, "kafka", k, "topic-a", "fanout-pipe1-group"], [0, "kafka", k, "topic-b", ""]]` | `[[0, 1]]` |
| `fanout-pipe2` | `[[1, "kafka", k, "topic-a", "fanout-pipe2-group"], [0, "kafka", k, "topic-c", ""]]` | `[[0, 1]]` |
| `fanout-sink1` | `[[1, "kafka", k, "topic-b", "fanout-sink1-group"]]` | `[]` |
| `fanout-sink2` | `[[1, "kafka", k, "topic-c", "fanout-sink2-group"]]` | `[]` |

Expected API output: two auto pipelines (section 4),
`fanout-src → fanout-sink1` and `fanout-src → fanout-sink2`.
