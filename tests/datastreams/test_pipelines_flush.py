"""Pipelines flush integration: envelopes, DSM payload, lifecycle (DSM2-531).

Covers TEST-doc T-8 (DSM-off envelope + POST), T-9 (DSM-on periodic payload is
byte-identical apart from the appended DdPipelinesReportV1 key), T-11 (a process
that never touches messaging starts no thread, registers no atexit hook and -
with the kill switch off - pulls no messaging client library) and T-12 (a forked
child gets a fresh window with no duplicated report).
"""

import gzip
import os
from unittest import mock

import msgpack

import ddtrace
from ddtrace.internal.datastreams import pipelines
from ddtrace.internal.datastreams import processor as processor_module
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.settings._config import config

from .test_pipelines import FakeClock
from .test_pipelines_golden import build_golden_report
from .test_pipelines_golden import record_golden_events


# Subprocess tests must import the worktree ddtrace, not any site-packages copy.
WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(ddtrace.__file__)))


def _subprocess_env(**overrides):
    env = os.environ.copy()
    env["PYTHONPATH"] = WORKTREE_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update(overrides)
    return env


def _decode_payload(payload):
    return msgpack.unpackb(gzip.decompress(payload), raw=False, strict_map_key=False)


def _map_header(data):
    """Return (header_length, entry_count) of a msgpack map."""
    first = data[0]
    if 0x80 <= first <= 0x8F:
        return 1, first - 0x80
    if first == 0xDE:
        return 3, int.from_bytes(data[1:3], "big")
    if first == 0xDF:
        return 5, int.from_bytes(data[1:5], "big")
    raise AssertionError("not a msgpack map: %r" % data[:1])


# -- T-8: DSM-off envelope and POST ------------------------------------------


def test_t8_dsm_off_envelope_and_post():
    # build the expected report first: the same events are deterministic, with
    # the service/env the flusher itself reads from the config
    flush_service = config._get_service(pipelines.DEFAULT_SERVICE_NAME)
    flush_env = config.env or ""
    twin = pipelines.PipelinesReporter(clock=FakeClock())
    record_golden_events(twin)
    expected_report = twin.take_report(flush_service, flush_env)

    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    record_golden_events(reporter)
    flusher = pipelines._StandaloneFlusher(reporter)
    conn = mock.Mock()
    conn.getresponse.return_value.status = 200
    try:
        with mock.patch.object(pipelines, "get_connection", return_value=conn):
            flusher.flush_once(force=True)

        assert conn.request.call_count == 1
        args, _ = conn.request.call_args
        assert args[0] == "POST"
        assert args[1] == "/v0.1/pipeline_stats"
        headers = args[3]
        assert headers["Content-Type"] == "application/msgpack"
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Datadog-Meta-Lang"] == "python"

        envelope = _decode_payload(args[2])
        assert envelope["Stats"] == []
        assert envelope["Lang"] == "python"
        assert envelope["TracerVersion"] == pipelines.__version__
        assert "Hostname" in envelope
        # the report key is the LAST key of the payload
        assert list(envelope)[-1] == "DdPipelinesReportV1"
        # ... and its bytes are the pinned encoding of the report
        assert envelope["DdPipelinesReportV1"] == msgpack.packb(expected_report, use_bin_type=True)
    finally:
        flusher.stop(timeout=2)


# -- T-9: DSM-on periodic payload ---------------------------------------------


def test_t9_dsm_on_periodic_appends_report_key_last():
    processor = DataStreamsProcessor("http://localhost:8126")
    try:
        # deterministic buckets without DDSketch (native .so skew in this env)
        def record_bucket():
            processor.track_kafka_produce("topicA", 1, 34, 1642544540)

        # a real reporter with a due window: the report bytes come from the
        # pinned encoding, not a stub
        clock = FakeClock()
        reporter = pipelines.PipelinesReporter(interval=300.0, clock=clock)
        record_golden_events(reporter)
        clock.advance(300.0)

        captured = []
        with mock.patch.object(processor_module, "get_pipelines_reporter", return_value=reporter):
            with mock.patch.object(processor, "_flush_stats_with_backoff", side_effect=captured.append):
                record_bucket()
                processor.periodic()  # with a report
                record_bucket()
                processor.periodic()  # window taken: without a report

        assert len(captured) == 2, captured
        with_report, without_report = captured
        with_decoded = _decode_payload(with_report)
        without_decoded = _decode_payload(without_report)

        # the classic payload is unchanged apart from the appended key
        assert set(with_decoded) - set(without_decoded) == {pipelines.REPORT_PAYLOAD_KEY}
        for key in without_decoded:
            assert with_decoded[key] == without_decoded[key], key
        # ... which is the LAST key ...
        assert list(with_decoded)[-1] == pipelines.REPORT_PAYLOAD_KEY
        # ... and carries the pinned encoding of the golden report, built with
        # the same service/env the processor passes to the reporter
        assert with_decoded[pipelines.REPORT_PAYLOAD_KEY] == msgpack.packb(
            build_golden_report(processor._service, config.env or ""), use_bin_type=True
        )

        # Byte-level: the with-report payload is the without-report payload with
        # only the map-count header changed and the key/value pair appended at
        # the tail. (A strict full-byte prefix is impossible for a msgpack map:
        # appending a map entry necessarily rewrites the entry-count header, so
        # this header-adjusted prefix is the exact equivalent property.)
        with_raw, without_raw = gzip.decompress(with_report), gzip.decompress(without_report)
        with_header = _map_header(with_raw)
        without_header = _map_header(without_raw)
        assert with_header[1] == without_header[1] + 1
        assert with_raw[with_header[0] :].startswith(without_raw[without_header[0] :])
    finally:
        # stop() hits the known native .so API skew in this environment; the
        # leftover daemon thread is harmless (its buckets are drained).
        try:
            processor.stop()
        except Exception:  # noqa: BLE001
            pass


# -- T-11: no messaging, no lifecycle -----------------------------------------


def test_t11a_default_env_starts_no_thread_and_no_atexit_hook(run_python_code_in_subprocess):
    code = """
import atexit
import threading
import time

import ddtrace

# Let any import-time threads settle, then snapshot.
time.sleep(0.5)
threads_before = threading.active_count()
names_before = sorted(t.name for t in threading.enumerate())
atexit_before = atexit._ncallbacks()

from ddtrace.internal.datastreams import pipelines

reporter = pipelines.get_pipelines_reporter()
assert reporter is not None, reporter
assert pipelines._reporter is reporter
assert pipelines._flusher is None
assert not pipelines._lifecycle_started
assert not any("pipelines" in t.name for t in threading.enumerate())

time.sleep(0.5)
assert threading.active_count() == threads_before, threading.enumerate()
assert sorted(t.name for t in threading.enumerate()) == names_before
assert atexit._ncallbacks() == atexit_before
print("OK")
"""
    env = _subprocess_env(
        DD_DATA_STREAMS_PIPELINES_ENABLED="true",
        DD_DATA_STREAMS_ENABLED="false",
    )
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == 0, (out, err)
    assert b"OK" in out


def test_t11b_kill_switch_off_pulls_no_messaging_client_library(run_python_code_in_subprocess):
    code = """
import sys

import ddtrace

client_libs = (
    "confluent_kafka",
    "aiokafka",
    "kombu",
    "botocore",
    "boto3",
    "google.cloud.pubsub_v1",
)
for lib in client_libs:
    assert lib not in sys.modules, lib

from ddtrace.internal.settings._config import config
assert config._data_streams_pipelines_enabled is False
assert config._data_streams_enabled is False
assert "ddtrace.internal.datastreams.kafka" not in sys.modules
assert "ddtrace.internal.datastreams.pipelines" not in sys.modules

from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter
assert get_pipelines_reporter() is None
print("OK")
"""
    env = _subprocess_env(
        DD_DATA_STREAMS_PIPELINES_ENABLED="false",
        DD_DATA_STREAMS_ENABLED="false",
    )
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == 0, (out, err)
    assert b"OK" in out


# -- T-12: fork gives the child a fresh window ---------------------------------


def test_t12_fork_child_gets_fresh_window(run_python_code_in_subprocess):
    code = """
import os

from ddtrace.internal.datastreams import pipelines

reporter = pipelines.get_pipelines_reporter()
assert reporter is not None
reporter.set_current_consume("kafka", "c1", "parent-topic", "g")
reporter.on_produce("kafka", "c1", "parent-out")
assert pipelines._flusher is not None
old_flusher = pipelines._flusher

pid = os.fork()
if pid == 0:
    # Child: fresh window, no inherited flush thread, no duplicated report.
    try:
        assert pipelines._flusher is None
        assert not pipelines._lifecycle_started
        report = pipelines._reporter.take_report("svc", "")
        assert report is None, report
        # a new side starts a fresh window and lifecycle in the child
        pipelines._reporter.on_produce("sqs", "", "child-topic")
        assert pipelines._flusher is not None
        assert pipelines._flusher is not old_flusher
        report = pipelines._reporter.take_report("svc", "")
        sides = {tuple(side) for side in report["sides"]}
        assert ("produce", "sqs", "", "child-topic", "") in sides
        assert not any("parent" in str(side) for side in sides), sides
        print("CHILD-OK")
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)

_, status = os.waitpid(pid, 0)
assert os.WIFEXITED(status), status
assert os.WEXITSTATUS(status) == 0, status
print("OK")
"""
    env = _subprocess_env(
        DD_DATA_STREAMS_PIPELINES_ENABLED="true",
        DD_DATA_STREAMS_ENABLED="false",
    )
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == 0, (out, err)
    assert b"OK" in out
