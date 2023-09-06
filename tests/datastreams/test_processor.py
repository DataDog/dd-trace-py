import time

from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.datastreams.processor import PartitionKey


def test_data_streams_processor():
    processor = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], now, 1, 1)
    processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], now, 1, 2)
    processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], now, 1, 4)
    processor.on_checkpoint_creation(2, 4, ["direction:in", "topic:topicA", "type:kafka"], now, 1, 2)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    aggr_key_1 = (",".join(["direction:out", "topic:topicA", "type:kafka"]), 1, 2)
    aggr_key_2 = (",".join(["direction:in", "topic:topicA", "type:kafka"]), 2, 4)
    assert processor._buckets[bucket_time_ns].pathway_stats[aggr_key_1].full_pathway_latency.count == 3
    assert processor._buckets[bucket_time_ns].pathway_stats[aggr_key_2].full_pathway_latency.count == 1
    assert (
        abs(processor._buckets[bucket_time_ns].pathway_stats[aggr_key_1].full_pathway_latency.get_quantile_value(1) - 4)
        <= 4 * 0.008
    )  # relative accuracy of 0.00775
    assert (
        abs(processor._buckets[bucket_time_ns].pathway_stats[aggr_key_2].full_pathway_latency.get_quantile_value(1) - 2)
        <= 2 * 0.008
    )  # relative accuracy of 0.00775


def test_data_streams_loop_protection():
    processor = DataStreamsProcessor("http://localhost:8126")
    ctx = processor.set_checkpoint(["direction:in", "topic:topicA", "type:kafka"])
    parent_hash = ctx.hash
    processor.set_checkpoint(["direction:out", "topic:topicB", "type:kafka"])
    # the application sends data downstream to two different places.
    # Use the consume checkpoint as the parent
    child_hash = processor.set_checkpoint(["direction:out", "topic:topicB", "type:kafka"]).hash
    expected_child_hash = ctx._compute_hash(["direction:out", "topic:topicB", "type:kafka"], parent_hash)
    assert child_hash == expected_child_hash


def test_kafka_offset_monitoring():
    processor = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    processor.track_kafka_commit("group1", "topic1", 1, 10, now)
    processor.track_kafka_commit("group1", "topic1", 1, 14, now)
    processor.track_kafka_produce("topic1", 1, 34, now)
    processor.track_kafka_produce("topic1", 2, 10, now)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    assert processor._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 1)] == 34
    assert processor._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 2)] == 10
    assert processor._buckets[bucket_time_ns].latest_commit_offsets[ConsumerPartitionKey("group1", "topic1", 1)] == 14
