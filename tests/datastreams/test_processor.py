import gzip
import logging
import os
import time

import mock
import msgpack
import pytest

from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.processor import PartitionKey


processor = DataStreamsProcessor("http://localhost:8126")
mocked_time = 1642544540


def _decode_datastreams_payload(payload):
    decompressed = gzip.decompress(payload)
    decoded = msgpack.unpackb(decompressed, raw=False, strict_map_key=False)

    return decoded


def test_periodic_payload_process_tags():
    processor = DataStreamsProcessor("http://localhost:8126")
    try:
        captured_payloads = []
        with mock.patch.object(processor, "_flush_stats_with_backoff", side_effect=captured_payloads.append):
            processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], 1642544540, 1, 1)
            processor.periodic()

        assert captured_payloads, "expected periodic to send a payload"
        decoded = _decode_datastreams_payload(captured_payloads[0])
        assert decoded["Service"] == processor._service
        assert decoded["TracerVersion"] == processor._version
        assert decoded["Lang"] == "python"
        assert decoded["Hostname"] == processor._hostname
        assert "ProcessTags" in decoded
        assert isinstance(decoded["ProcessTags"], list)
        assert all(isinstance(x, str) and ":" in x for x in decoded["ProcessTags"])
    finally:
        processor.stop()
        processor.join()


def test_periodic_logs_warning_on_flush_failure(caplog):
    # AIDEV-NOTE: DSMS-144 — when retry-budget is exhausted, the customer-facing log
    # must (a) be WARNING (not ERROR) so it doesn't trip alerting on benign flush
    # failures, (b) preserve the leading phrase for customers' existing log-based
    # alerts, (c) explain impact, (d) include the exception cause inline for triage,
    # and (e) NOT include a multi-line traceback (which is what made the original
    # ERROR-level message look like a crash).
    processor = DataStreamsProcessor("http://localhost:8126")
    try:
        with mock.patch.object(
            processor,
            "_flush_stats_with_backoff",
            side_effect=TimeoutError("timed out"),
        ):
            processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:topicA", "type:kafka"], 1642544540, 1, 1)
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.datastreams.processor"):
                processor.periodic()

        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "ddtrace.internal.datastreams.processor"
        ]

        assert len(warning_records) == 1
        msg = warning_records[0].getMessage()
        assert "retry limit exceeded submitting pathway stats" in msg
        assert "last 10 seconds of DSM data is dropped" in msg
        assert "timed out" in msg
        assert warning_records[0].exc_info is None
    finally:
        processor.stop()
        processor.join()


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


@pytest.mark.subprocess()
def test_new_pathway_uses_container_tags_hash():
    from ddtrace.internal.datastreams.processor import DataStreamsProcessor
    from ddtrace.internal.process_tags import compute_base_hash

    processor = DataStreamsProcessor("http://localhost:8126")
    mocked_time = 1642544540

    ctx = processor.new_pathway(now_sec=mocked_time)
    ctx.set_checkpoint(["direction:out", "topic:topicA", "type:kafka"])
    hash_without_base = ctx.hash

    compute_base_hash("container-hash-123")
    ctx_with_base = processor.new_pathway(now_sec=mocked_time)
    ctx_with_base.set_checkpoint(["direction:out", "topic:topicA", "type:kafka"])
    hash_with_base = ctx_with_base.hash

    assert hash_without_base != hash_with_base


@pytest.mark.subprocess()
def test_new_pathway_uses_process_tags_hash_without_compute_base_hash():
    from ddtrace.internal import process_tags
    from ddtrace.internal.datastreams.processor import DataStreamsProcessor

    processor = DataStreamsProcessor("http://localhost:8126")
    mocked_time = 1642544540
    tags = ["direction:out", "topic:topicA", "type:kafka"]

    # Trigger lazy process tags initialization, which should compute a base hash even
    # before we receive any container hash from the agent.
    _ = process_tags.process_tags
    assert process_tags.base_hash is not None
    assert process_tags.base_hash_bytes

    ctx_with_base = processor.new_pathway(now_sec=mocked_time)
    ctx_with_base.set_checkpoint(tags)
    hash_with_base = ctx_with_base.hash

    assert hash_with_base is not None


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
    # Use a standalone processor to avoid shared-state races with the module-level processor's
    # background thread (which drains buckets every 10s) and cross-test contamination.
    p = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    p.track_kafka_commit("group1", "topic1", 1, 10, now)
    p.track_kafka_commit("group1", "topic1", 1, 14, now)
    p.track_kafka_produce("topic1", 1, 34, now)
    p.track_kafka_produce("topic1", 2, 10, now)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    assert p._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 1, "")] == 34
    assert p._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 2, "")] == 10
    assert p._buckets[bucket_time_ns].latest_commit_offsets[ConsumerPartitionKey("group1", "topic1", 1, "")] == 14


def test_kafka_offset_monitoring_with_cluster_id():
    # Use a standalone processor to avoid shared-state races and cross-test contamination.
    p = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    cluster = "test-cluster-abc"
    p.track_kafka_commit("group1", "topic1", 1, 10, now, cluster_id=cluster)
    p.track_kafka_commit("group1", "topic1", 1, 14, now, cluster_id=cluster)
    p.track_kafka_produce("topic1", 1, 34, now, cluster_id=cluster)
    p.track_kafka_produce("topic1", 2, 10, now, cluster_id=cluster)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    assert p._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 1, cluster)] == 34
    assert p._buckets[bucket_time_ns].latest_produce_offsets[PartitionKey("topic1", 2, cluster)] == 10
    assert p._buckets[bucket_time_ns].latest_commit_offsets[ConsumerPartitionKey("group1", "topic1", 1, cluster)] == 14

    # Verify cluster_id is present in bucket keys -- safe to iterate all keys since this
    # standalone processor only contains entries from this test.
    bucket = p._buckets[bucket_time_ns]
    for key in bucket.latest_commit_offsets:
        assert key.cluster_id == cluster
    for key in bucket.latest_produce_offsets:
        assert key.cluster_id == cluster


def test_kafka_offset_monitoring_without_cluster_id_omits_tag():
    """When cluster_id is empty, the kafka_cluster_id tag should not appear in bucket keys."""
    # Use a standalone processor to avoid shared-state races and cross-test contamination.
    p = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    p.track_kafka_commit("group1", "topic_no_cluster", 0, 5, now)
    p.track_kafka_produce("topic_no_cluster", 0, 20, now)
    now_ns = int(now * 1e9)
    bucket_time_ns = int(now_ns - (now_ns % 1e10))
    bucket = p._buckets[bucket_time_ns]
    commit_key = ConsumerPartitionKey("group1", "topic_no_cluster", 0, "")
    produce_key = PartitionKey("topic_no_cluster", 0, "")
    assert bucket.latest_commit_offsets[commit_key] == 5
    assert bucket.latest_produce_offsets[produce_key] == 20
    assert commit_key.cluster_id == ""
    assert produce_key.cluster_id == ""


def test_kafka_offset_serialization_cluster_id_tag():
    """Verify _serialize_buckets emits kafka_cluster_id tag when present and omits it when empty."""
    # Use a standalone processor so _serialize_buckets() doesn't drain the shared one.
    p = DataStreamsProcessor("http://localhost:8126")
    now = time.time()
    cluster = "serialize-test-cluster"
    p.track_kafka_commit("grp", "topic_has_cluster", 0, 10, now, cluster_id=cluster)
    p.track_kafka_produce("topic_has_cluster", 0, 20, now, cluster_id=cluster)
    p.track_kafka_commit("grp", "topic_no_cluster", 0, 5, now)
    p.track_kafka_produce("topic_no_cluster", 0, 15, now)

    serialized = p._serialize_buckets()
    all_backlogs = []
    for s in serialized:
        all_backlogs.extend(s.get("Backlogs", []))

    with_cluster = [b for b in all_backlogs if any(t == "topic:topic_has_cluster" for t in b["Tags"])]
    without_cluster = [b for b in all_backlogs if any(t == "topic:topic_no_cluster" for t in b["Tags"])]

    assert len(with_cluster) >= 1
    for b in with_cluster:
        assert "kafka_cluster_id:" + cluster in b["Tags"]

    assert len(without_cluster) >= 1
    for b in without_cluster:
        assert all(not t.startswith("kafka_cluster_id:") for t in b["Tags"])


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


def test_processor_flushes_on_sigint(ddtrace_run_python_code_in_subprocess):
    """DataStreamsProcessor must flush on SIGINT without producing a ddtrace traceback."""
    code = """
import os
import signal
import time
from ddtrace.internal.datastreams.processor import DataStreamsProcessor

def fake_flush(*args, **kwargs):
    print("Fake flush called", flush=True)

processor = DataStreamsProcessor("http://localhost:8126")
processor._flush_stats_with_backoff = fake_flush
now = time.time()
processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:test", "type:kafka"], now, 1, 1)
os.kill(os.getpid(), signal.SIGINT)
"""
    env = os.environ.copy()
    env["DD_DATA_STREAMS_ENABLED"] = "True"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env, timeout=5)
    assert "Fake flush called" in out.decode()
    assert b"wrap_signals" not in err


def test_processor_flushes_on_sigint_asyncio(ddtrace_run_python_code_in_subprocess):
    """DataStreamsProcessor must flush via atexit when SIGINT interrupts asyncio.run().

    Under asyncio.Runner, SIGINT is consumed by asyncio's own handler rather than
    Python's default_int_handler, so the flush relies on the atexit.register hook
    added alongside register_on_exit_signal.
    """
    code = """
import asyncio
import os
import signal
import time
from ddtrace.internal.datastreams.processor import DataStreamsProcessor

def fake_flush(*args, **kwargs):
    print("Fake flush called", flush=True)

processor = DataStreamsProcessor("http://localhost:8126")
processor._flush_stats_with_backoff = fake_flush
now = time.time()
processor.on_checkpoint_creation(1, 2, ["direction:out", "topic:test", "type:kafka"], now, 1, 1)

async def main():
    asyncio.get_event_loop().call_later(0.1, os.kill, os.getpid(), signal.SIGINT)
    await asyncio.sleep(10)

asyncio.run(main())
"""
    env = os.environ.copy()
    env["DD_DATA_STREAMS_ENABLED"] = "True"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env, timeout=5)
    assert "Fake flush called" in out.decode()
    assert b"wrap_signals" not in err


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
