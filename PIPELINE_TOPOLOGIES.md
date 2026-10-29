# Demo pipeline topologies — automatic DSM pipelines from APM services (DSM2-531)

This document pins, for every local demo in `pipelines-demo/`, the
participating services, the exact expected pipelines (ordered node lists), and
the expected per-process tracer state, so the end-to-end run can be verified
against the dd-source `dsm-auto-pipelines` harness and `dsm-api` output.

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
- LocalStack uses account `000000000000` and region `us-east-1`, so the demo
  SNS topic ARN is `arn:aws:sns:us-east-1:000000000000:sns-demo-topic`.

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

## 5. Per-system source-pipe-sink (X-2)

Each system runs the same 5-node shape, with the classification notes from
CONTRACT.md §1. All apps are in `pipelines-demo/apps/`, resources created by
the matching `pipelines-demo/setup/setup_*.py` script.

### 5a. SQS

Queues `sqs-a`, `sqs-b`; services `sqs-src`, `sqs-pipe`, `sqs-sink`.

```
sqs-src → sqs-a (topic, sqs, cluster="") → sqs-pipe → sqs-b (topic, sqs, cluster="") → sqs-sink
```

### 5b. SNS → SQS

Services `sns-publisher`, `sns-subscriber`. The publisher only talks to SNS;
delivery to the subscriber happens through the queue subscription created by
`setup/setup_sns.py` (non-raw delivery, so the SNS message envelope carries
the TopicArn). The tracer classifies the subscriber's consume side as
`sns`/`TopicArn` — **not** `sqs`/queue — which is what joins it to the SNS
topic produce side:

```
sns-publisher → sns-demo-topic (topic, sns, cluster="", name=arn:aws:sns:us-east-1:000000000000:sns-demo-topic) → sns-subscriber
```

### 5c. Kinesis

Streams `kin-a`, `kin-b`; services `kinesis-src`, `kinesis-pipe`,
`kinesis-sink`.

```
kinesis-src → kin-a (topic, kinesis, cluster="") → kinesis-pipe → kin-b (topic, kinesis, cluster="") → kinesis-sink
```

### 5d. EventBridge → SQS

Services `eb-publisher`, `eb-subscriber`. The publisher calls `PutEvents` with
`DetailType=demo-order` on the default bus; `setup/setup_eventbridge.py`
creates the rule (`source = demo.src`) and its SQS target queue
`eb-demo-queue`. Produce side: `eventbridge`/`demo-order` (the bus name is
**not** the side key); the subscriber polls the SQS queue and its consume side
is classified `eventbridge`/`demo-order` from the event envelope:

```
eb-publisher → demo-order (topic, eventbridge, cluster="") → eb-subscriber
```

### 5e. Pub/Sub (emulator)

Project `demo-project`; topics `ps-topic-a`, `ps-topic-b`; subscriptions
`ps-sub-a`, `ps-sub-b` created from the topics by `setup/setup_pubsub.py`.
Apps call `get_subscription` at startup so the tracer learns the
subscription → topic mapping (CONTRACT.md §1 pubsub notes): the consume side
is recorded against the **topic** with the subscription id as the group.
Services `pubsub-src`, `pubsub-pipe`, `pubsub-sink`.

```
pubsub-src → ps-topic-a (topic, pubsub, cluster="") → pubsub-pipe → ps-topic-b (topic, pubsub, cluster="") → pubsub-sink
```

### 5f. RabbitMQ (kombu)

Queues `rmq-a`, `rmq-b` on the **default exchange**, routing key = queue name
(both for produce and consume, so the side topic is the queue name). Services
`rmq-src`, `rmq-pipe`, `rmq-sink`.

```
rmq-src → rmq-a (topic, rabbitmq, cluster="") → rmq-pipe → rmq-b (topic, rabbitmq, cluster="") → rmq-sink
```

## 6. Mixed (X-3)

Kafka source into a cross-system pipe: services `kafka-src`,
`kafka-sqs-pipe` (Kafka consume of `topic-a` in group `mixed-pipe-group`, SQS
produce to queue `sqs-a`), and `sqs-sink` (consumes `sqs-a`). The in-process
link inside `kafka-sqs-pipe` glues the `kafka` consume side to the `sqs`
produce side, and the backend's topic matching follows each side's own
`queue_type`, so this is **ONE pipeline across both systems**:

```
kafka-src
→ topic-a (topic, kafka, cluster=localhost:9092)
→ kafka-sqs-pipe (service)
→ sqs-a (topic, sqs, cluster="")
→ sqs-sink (service)
```

Name in the API: `kafka-src → sqs-sink`.

## 7. State: DSM OFF, expected reports per process, expected API output

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

### 5. Per-system

SQS:

| Process | sides | links |
|---|---|---|
| `sqs-src` | `[[0, "sqs", "", "sqs-a", ""]]` | `[[-1, 0]]` |
| `sqs-pipe` | `[[1, "sqs", "", "sqs-a", ""], [0, "sqs", "", "sqs-b", ""]]` | `[[0, 1]]` |
| `sqs-sink` | `[[1, "sqs", "", "sqs-b", ""]]` | `[]` |

SNS → SQS (consume side classified `sns`/TopicArn, not `sqs`/queue):

| Process | sides | links |
|---|---|---|
| `sns-publisher` | `[[0, "sns", "", "arn:aws:sns:us-east-1:000000000000:sns-demo-topic", ""]]` | `[[-1, 0]]` |
| `sns-subscriber` | `[[1, "sns", "", "arn:aws:sns:us-east-1:000000000000:sns-demo-topic", ""]]` | `[]` |

Kinesis:

| Process | sides | links |
|---|---|---|
| `kinesis-src` | `[[0, "kinesis", "", "kin-a", ""]]` | `[[-1, 0]]` |
| `kinesis-pipe` | `[[1, "kinesis", "", "kin-a", ""], [0, "kinesis", "", "kin-b", ""]]` | `[[0, 1]]` |
| `kinesis-sink` | `[[1, "kinesis", "", "kin-b", ""]]` | `[]` |

EventBridge → SQS (produce side `eventbridge`/`<detail-type>`, consume side
classified `eventbridge`/`<detail-type>`):

| Process | sides | links |
|---|---|---|
| `eb-publisher` | `[[0, "eventbridge", "", "demo-order", ""]]` | `[[-1, 0]]` |
| `eb-subscriber` | `[[1, "eventbridge", "", "demo-order", ""]]` | `[]` |

Pub/Sub (consume side recorded against the topic, group = subscription id):

| Process | sides | links |
|---|---|---|
| `pubsub-src` | `[[0, "pubsub", "", "ps-topic-a", ""]]` | `[[-1, 0]]` |
| `pubsub-pipe` | `[[1, "pubsub", "", "ps-topic-a", "ps-sub-a"], [0, "pubsub", "", "ps-topic-b", ""]]` | `[[0, 1]]` |
| `pubsub-sink` | `[[1, "pubsub", "", "ps-topic-b", "ps-sub-b"]]` | `[]` |

RabbitMQ (default exchange, routing key = queue name):

| Process | sides | links |
|---|---|---|
| `rmq-src` | `[[0, "rabbitmq", "", "rmq-a", ""]]` | `[[-1, 0]]` |
| `rmq-pipe` | `[[1, "rabbitmq", "", "rmq-a", ""], [0, "rabbitmq", "", "rmq-b", ""]]` | `[[0, 1]]` |
| `rmq-sink` | `[[1, "rabbitmq", "", "rmq-b", ""]]` | `[]` |

Expected API output: one auto pipeline per system as pinned in section 5
(`sqs-src → sqs-sink`, `sns-publisher → sns-subscriber`,
`kinesis-src → kinesis-sink`, `eb-publisher → eb-subscriber`,
`pubsub-src → pubsub-sink`, `rmq-src → rmq-sink`), with the topic nodes
carrying each system's `queue_type`.

### 6. Mixed (X-3)

| Process | sides | links |
|---|---|---|
| `kafka-src` | `[[0, "kafka", k, "topic-a", ""]]` | `[[-1, 0]]` |
| `kafka-sqs-pipe` | `[[1, "kafka", k, "topic-a", "mixed-pipe-group"], [0, "sqs", "", "sqs-a", ""]]` | `[[0, 1]]` |
| `sqs-sink` | `[[1, "sqs", "", "sqs-a", ""]]` | `[]` |

Expected API output: **one** auto pipeline
`kafka-src → kafka-sqs-pipe → sqs-sink` with node list
`kafka-src (service), topic-a (topic, kafka, localhost:9092), kafka-sqs-pipe
(service), sqs-a (topic, sqs, ""), sqs-sink (service)` — a single pipeline
across both systems (name `kafka-src → sqs-sink`).
