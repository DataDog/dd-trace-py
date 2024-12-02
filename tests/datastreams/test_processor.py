import os
import time

import mock

from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.processor import PartitionKey


processor = DataStreamsProcessor("http://localhost:8126")
mocked_time = 1642544540


def test_data_streams_processor():
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


def test_data_streams_loop_protection():
    ctx = processor.set_checkpoint(["direction:in", "topic:topicA", "type:kafka"])
    parent_hash = ctx.hash
    processor.set_checkpoint(["direction:out", "topic:topicB", "type:kafka"])
    # the application sends data downstream to two different places.
    # Use the consume checkpoint as the parent
    child_hash = processor.set_checkpoint(["direction:out", "topic:topicB", "type:kafka"]).hash
    expected_child_hash = ctx._compute_hash(["direction:out", "topic:topicB", "type:kafka"], parent_hash)
    assert child_hash == expected_child_hash


def test_kafka_offset_monitoring():
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

def run_test():
    processor = DataStreamsProcessor("http://localhost:8126")
    processor._flush_stats_with_backoff = fake_flush
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


@mock.patch("time.time", mock.MagicMock(return_value=mocked_time))
def test_dsm_pathway_codec_encode_base64():
    encoded_string = "10nVzXmeKoDApcX0zV/ApcX0zV8="  # pathway hash is: 9235368231858162135

    ctx = processor.new_pathway()
    ctx.hash = 9235368231858162135

    assert ctx.pathway_start_sec == mocked_time
    assert ctx.current_edge_start_sec == mocked_time

    carrier = {}
    DsmPathwayCodec.encode(ctx, carrier)

    assert PROPAGATION_KEY_BASE_64 in carrier
    assert carrier[PROPAGATION_KEY_BASE_64] == encoded_string


def test_dsm_pathway_codec_decode_base64():
    encoded_string = "10nVzXmeKoDApcX0zV/ApcX0zV8="  # pathway hash is: 9235368231858162135
    decoded_hash = 9235368231858162135

    carrier = {PROPAGATION_KEY_BASE_64: encoded_string}
    ctx = DsmPathwayCodec.decode(carrier, processor)

    assert ctx.hash == decoded_hash
    assert ctx.pathway_start_sec == mocked_time
    assert ctx.current_edge_start_sec == mocked_time


def test_dsm_pathway_codec_decode_base64_deprecated_context_key():
    encoded_string = "10nVzXmeKoDApcX0zV/ApcX0zV8="  # pathway hash is: 9235368231858162135
    decoded_hash = 9235368231858162135

    carrier = {PROPAGATION_KEY: encoded_string}
    ctx = DsmPathwayCodec.decode(carrier, processor)

    assert ctx.hash == decoded_hash
    assert ctx.pathway_start_sec == mocked_time
    assert ctx.current_edge_start_sec == mocked_time


def test_dsm_pathway_codec_decode_byte_encoding():
    encoded_string = (
        b"\xd7I\xd5\xcdy\x9e*\x80\xc0\xa5\xc5\xf4\xcd_\xc0\xa5\xc5\xf4\xcd_"  # pathway hash is: 9235368231858162135
    )
    decoded_hash = 9235368231858162135

    carrier = {PROPAGATION_KEY: encoded_string}
    ctx = DsmPathwayCodec.decode(carrier, processor)

    assert ctx.hash == decoded_hash
    assert ctx.pathway_start_sec == mocked_time
    assert ctx.current_edge_start_sec == mocked_time


@mock.patch("time.time", mock.MagicMock(return_value=mocked_time))
def test_dsm_pathway_codec_decode_no_context():
    carrier = {}
    ctx = DsmPathwayCodec.decode(carrier, processor)

    assert ctx.hash == processor.new_pathway().hash
    assert ctx.pathway_start_sec == mocked_time
    assert ctx.current_edge_start_sec == mocked_time
