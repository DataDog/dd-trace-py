import sys
import typing as t

from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal import runtime
from ddtrace.internal.utils.http import parse_form_multipart
from tests.debugging.utils import create_snapshot_line_probe


class DummyProbeStatusLogger(ProbeStatusLogger):
    def __init__(self, *args, **kwargs):
        super(DummyProbeStatusLogger, self).__init__(*args, **kwargs)
        self._flush_queue = []

    def _write_payload(self, data: t.Tuple[bytes, dict]):
        body, headers = data
        self._flush_queue.extend(parse_form_multipart(body.decode("utf-8"), headers)["event"])

    def clear(self):
        self._flush_queue[:] = []

    @property
    def queue(self) -> list:
        self.flush()
        return self._flush_queue


def test_probe_status_received():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    message = "Probe %s received" % probe.probe_id

    status_logger.received(probe, message)

    (entry,) = status_logger.queue
    assert entry["message"] == message
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["probeVersion"] == probe.version
    assert entry["debugger"]["diagnostics"]["runtimeId"] == runtime.get_runtime_id()
    assert entry["debugger"]["diagnostics"]["status"] == "RECEIVED"


def test_probe_status_installed():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        version=123,
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )
    message = "Probe %s installed" % probe.probe_id

    status_logger.installed(probe, message)

    (entry,) = status_logger.queue
    assert entry["message"] == message
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["probeVersion"] == probe.version
    assert entry["debugger"]["diagnostics"]["runtimeId"] == runtime.get_runtime_id()
    assert entry["debugger"]["diagnostics"]["status"] == "INSTALLED"


def test_probe_status_error():
    status_logger = DummyProbeStatusLogger("test")

    probe = create_snapshot_line_probe(
        probe_id="probe-instance-method",
        source_file="tests/debugger/submod/stuff.py",
        line=36,
        condition=None,
    )

    try:
        raise RuntimeError("Test error")
    except Exception:
        exc_type, exc, _ = sys.exc_info()
        status_logger.error(probe, (exc_type.__name__, str(exc)))

    (entry,) = status_logger.queue
    assert entry["message"] == "Failed to instrument probe probe-instance-method"
    assert entry["debugger"]["diagnostics"]["probeId"] == probe.probe_id
    assert entry["debugger"]["diagnostics"]["status"] == "ERROR"

    exc = entry["debugger"]["diagnostics"]["exception"]
    assert exc["type"] == "RuntimeError"
    assert exc["message"] == "Test error"
