import builtins
import json
import logging
from unittest.mock import Mock

import pytest

from ddtrace.debugging._otel import OTLPDebuggerSender
from ddtrace.debugging._otel import OTLPProbeMetricsClient
from ddtrace.internal import debugger_transport
from ddtrace.internal.native import DebuggerTrackType


@pytest.fixture(autouse=True)
def reset_transport():
    debugger_transport.get_debugger_transport.cache_clear()
    yield
    debugger_transport.get_debugger_transport.cache_clear()


@pytest.mark.parametrize("flag", [None, "false", "0"])
def test_di_otel_disabled_by_default(monkeypatch, flag):
    monkeypatch.setattr(debugger_transport.env, "get", lambda key, default=None: flag or default)
    assert debugger_transport.get_debugger_transport() is None


def test_di_otel_missing_exporter(monkeypatch):
    monkeypatch.setattr(debugger_transport.env, "get", lambda key, default=None: "true")
    original = builtins.__import__

    def missing_exporter(name, *args, **kwargs):
        if name.startswith("opentelemetry.exporter"):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_exporter)
    assert debugger_transport.get_debugger_transport() is None


@pytest.mark.parametrize(
    "track,name",
    [
        (DebuggerTrackType.Logs, "dd.debugger.log"),
        (DebuggerTrackType.Snapshots, "dd.debugger.snapshot"),
        (DebuggerTrackType.Diagnostics, "dd.debugger.diagnostic"),
    ],
)
def test_di_otel_tracks_are_named_events(track, name):
    transport = Mock()
    payload = {"message": "probe output", "debugger": {"value": 2**100, "missing": None}}
    response = OTLPDebuggerSender(transport, "env:test").send(json.dumps([payload]).encode(), track)
    assert response.accepted
    transport.emit.assert_called_once_with(name, payload, "env:test")


def test_di_otel_metric_probe_instruments():
    transport = Mock()
    client = OTLPProbeMetricsClient(transport, "dynamic.instrumentation.metric")
    tags = {"probe_id": "test-probe"}
    client.increment("probe.count", 2, tags)
    client.increment("probe.count", 3, tags)
    client.gauge("probe.active", 4, tags)
    client.histogram("probe.duration", 5, tags)
    client.distribution("probe.size", 6, tags)
    transport.meter.create_counter.assert_called_once_with("dynamic.instrumentation.metric.probe.count")
    assert transport.meter.create_counter.return_value.add.call_count == 2
    transport.meter.create_gauge.return_value.set.assert_called_once_with(4, tags)
    assert transport.meter.create_histogram.return_value.record.call_count == 2


def test_di_otel_event_fidelity_and_no_logging_handler():
    sdk = pytest.importorskip("opentelemetry.sdk._logs")
    from opentelemetry import version

    if tuple(int(x) for x in version.__version__.split(".")[:2]) < (1, 45):
        pytest.skip("POC targets the current event-name API")
    from opentelemetry._logs import LogRecord
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

    root_handlers = list(logging.getLogger().handlers)
    provider = sdk.LoggerProvider(shutdown_on_exit=False)
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    transport = debugger_transport.DebuggerTransport(provider, Mock(), LogRecord)
    payload = {
        "message": "captured",
        "timestamp": 123456789,
        "dd": {"trace_id": "1234567890abcdef1234567890abcdef", "span_id": "123"},
        "debugger": {"snapshot": {"large": 2**100, "null": None, "redacted": "[REDACTED]"}},
    }
    transport.emit("dd.debugger.snapshot", payload, "env:test")
    record = exporter.get_finished_logs()[0].log_record
    assert record.event_name == "dd.debugger.snapshot"
    assert record.timestamp == 123456789000000
    assert record.trace_id == int(payload["dd"]["trace_id"], 16)
    assert record.span_id == 123
    assert json.loads(record.attributes["datadog.di.payload"]) == payload
    assert record.body == "captured"
    assert logging.getLogger().handlers == root_handlers
    provider.shutdown()
