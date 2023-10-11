import os
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


def test_processor_atexit(ddtrace_run_python_code_in_subprocess):
    code = """
import mock
import pytest
import sys
import time

from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.atexit import register_on_exit_signal

def fake_flush(*args, **kwargs):
    print("Fake flush called")

_exit = False
def set_exit():
    global _exit
    _exit = True

@mock.patch("ddtrace.internal.datastreams.processor.DataStreamsProcessor._flush_stats", new_callable=fake_flush)
def run_test(mock_flush):
    processor = DataStreamsProcessor("http://localhost:9126")
    processor.stop(5)  # Stop period processing/flushing

    now = time.time()
    processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], now, 1, 1)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    aggr_key = (",".join(["direction:out", "topic:topicA", "type:kafka"]), 1, 2)
    assert processor._buckets[bucket_time_ns].pathway_stats[aggr_key].full_pathway_latency.count == 1

    counter = 0
    while counter < 500 and not _exit:
        counter += 1
        time.sleep(0.1)

register_on_exit_signal(set_exit)
run_test()
"""

    env = os.environ.copy()
    env["DD_DATA_STREAMS_ENABLED"] = "True"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env, timeout=5)
    assert out.decode().strip() == "Fake flush called"


def test_threaded_import(ddtrace_run_python_code_in_subprocess):
    code = """
import pytest
import sys
import time
import threading

from ddtrace.internal.datastreams.processor import DataStreamsProcessor

def fake_flush(*args, **kwargs):
    print("Fake flush called")

def run_test():
    processor = DataStreamsProcessor("http://localhost:8126")

t = threading.Thread(target=run_test)
t.start()
t.join()
"""

    env = os.environ.copy()
    env["DD_DATA_STREAMS_ENABLED"] = "True"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env, timeout=5)
    assert err.decode().strip() == ""
