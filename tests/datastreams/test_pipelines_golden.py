"""Pipelines report windows and the golden report fixture (DSM2-531).

Covers TEST-doc T-6: one report per flush window (injected clock and the real
background thread), no report for an empty window, and a committed golden
fixture - a fixed event sequence must always produce the same
DdPipelinesReportV1 msgpack bytes.
"""

import gzip
from pathlib import Path
import time

import msgpack

from ddtrace.internal.datastreams import pipelines
from ddtrace.internal.settings._config import config

from .test_pipelines import FakeClock


HERE = Path(__file__).parent
GOLDEN_SERVICE = "golden-service"
GOLDEN_ENV = "prod"
GOLDEN_FIXTURE = HERE / "pipelines_golden_report.msgpack"


def record_golden_events(reporter):
    """The fixed event sequence encoded in the golden fixture (T-6)."""
    # produce without a consume parent: source link
    reporter.on_produce("kafka", "cluster-a", "orders")
    with reporter.on_consume("kafka", "cluster-a", "payments", "billing"):
        reporter.on_produce("sqs", "", "invoices")
    # the scope exited, so this produce is another source link to the same side
    reporter.on_produce("sqs", "", "invoices")
    # set_current_consume keeps the context until replaced (consumer-hook variant)
    reporter.set_current_consume("pubsub", "", "telemetry", "sub-1")
    reporter.on_produce("kinesis", "", "streams-1")
    # a recorded side that is entered and exited again
    with reporter.on_consume("eventbridge", "", "bus-1", ""):
        pass


def build_golden_report(service=GOLDEN_SERVICE, env=GOLDEN_ENV):
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    record_golden_events(reporter)
    return reporter.take_report(service, env)


# -- golden fixture -----------------------------------------------------------


def test_t6_golden_report_bytes_are_stable():
    report = build_golden_report()
    packed = pipelines.packb(report)
    assert packed == GOLDEN_FIXTURE.read_bytes()
    # pinned encoding: byte-identical to msgpack.packb(report, use_bin_type=True)
    assert packed == msgpack.packb(report, use_bin_type=True)
    # and the fixture decodes back to the same report
    assert msgpack.unpackb(GOLDEN_FIXTURE.read_bytes(), raw=False) == report


# -- one report per window (injected clock) -----------------------------------


def test_t6_one_report_per_window_injected_clock():
    clock = FakeClock()
    reporter = pipelines.PipelinesReporter(interval=300.0, clock=clock)
    reporter.on_produce("kafka", "", "orders")
    # the window has not elapsed yet
    assert reporter.take_due_report("svc", "") is None
    clock.advance(299.9)
    assert reporter.take_due_report("svc", "") is None
    clock.advance(0.1)
    report = reporter.take_due_report("svc", "")
    assert report is not None
    assert report["sides"] == [["produce", "kafka", "", "orders", ""]]
    # the window was taken: nothing left to report
    assert reporter.take_due_report("svc", "") is None
    # an empty window produces no report
    clock.advance(300.0)
    assert reporter.take_due_report("svc", "") is None
    # ... but the next window with traffic does
    reporter.set_current_consume("sqs", "", "queue", "group")
    clock.advance(300.0)
    report = reporter.take_due_report("svc", "")
    assert report is not None
    assert report["sides"] == [["consume", "sqs", "", "queue", "group"]]


# -- one report per window (the real background thread) -----------------------


def test_t6_background_thread_flushes_one_report_per_window():
    reporter = pipelines.PipelinesReporter(interval=0.05, clock=time.monotonic)
    sent = []
    flusher = pipelines._StandaloneFlusher(reporter)
    flusher._send = sent.append
    flusher.start()
    try:
        reporter.on_produce("kafka", "", "orders")
        deadline = time.monotonic() + 5.0
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sent, "expected the background flush thread to flush the window"

        # empty windows flush nothing: wait out several windows
        time.sleep(0.3)
        assert len(sent) == 1

        envelope = msgpack.unpackb(gzip.decompress(sent[0]), raw=False, strict_map_key=False)
        assert envelope["Stats"] == []
        assert list(envelope)[-1] == "DdPipelinesReportV1"
        assert envelope["Service"] == config._get_service(pipelines.DEFAULT_SERVICE_NAME)
        assert envelope["Lang"] == "python"

        # the report bytes match the pinned encoding of the same window
        twin = pipelines.PipelinesReporter(interval=0.05, clock=time.monotonic)
        twin.on_produce("kafka", "", "orders")
        expected = twin.take_report(envelope["Service"], config.env or "")
        assert envelope["DdPipelinesReportV1"] == msgpack.packb(expected, use_bin_type=True)
    finally:
        flusher.stop(timeout=2)
