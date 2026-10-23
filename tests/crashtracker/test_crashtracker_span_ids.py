import os
import sys

import pytest

import tests.crashtracker.utils as utils


crash_with_active_span_code = """
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ctypes
import sys
import ddtrace.auto
from ddtrace import tracer

with tracer.trace("test.operation") as span:
    sys.stdout.write(str(span.span_id) + "\\n")
    sys.stdout.write(str(span.trace_id) + "\\n")
    sys.stdout.flush()
    ctypes.string_at(0)
sys.exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_crash_report_includes_span_ids(run_python_code_in_subprocess):
    """Crash while a span is active; verify the exact span and trace IDs appear in the report."""
    import json

    service = "test_crashtracker_span_ids"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(crash_with_active_span_code, env=env)

        assert exitcode in (-11, -7), f"Expected crash signal, got {exitcode}"

        lines = stdout.decode().strip().split("\n")
        expected_span_id = lines[0]
        expected_trace_id = lines[1]

        _ping = utils.get_crash_ping(client, service=service)
        report = utils.get_crash_report(client, service=service)

        body = json.loads(report["body"])
        message = json.loads(body["payload"]["logs"][0]["message"])

        reported_span_ids = {s["id"] for s in message["span_ids"]}
        reported_trace_ids = {t["id"] for t in message["trace_ids"]}

        assert expected_span_id in reported_span_ids, (
            f"span_id {expected_span_id} not found in crash report span_ids: {reported_span_ids}"
        )
        assert expected_trace_id in reported_trace_ids, (
            f"trace_id {expected_trace_id} not found in crash report trace_ids: {reported_trace_ids}"
        )


crash_no_active_span_code = """
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ctypes
import ddtrace.auto
from ddtrace import tracer

# Create and finish a span before crashing
with tracer.trace("test.operation"):
    pass

# Crash outside any span
ctypes.string_at(0)
sys.exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_crash_report_no_span_ids_when_no_active_span(run_python_code_in_subprocess):
    """Crash after all spans have finished; span_ids/trace_ids should be empty."""
    import json

    service = "test_crashtracker_no_span_ids"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(crash_no_active_span_code, env=env)

        assert exitcode in (-11, -7), f"Expected crash signal, got {exitcode}"

        _ping = utils.get_crash_ping(client, service=service)
        report = utils.get_crash_report(client, service=service)

        body = json.loads(report["body"])
        message = json.loads(body["payload"]["logs"][0]["message"])

        span_ids = message.get("span_ids", [])
        trace_ids = message.get("trace_ids", [])
        assert len(span_ids) == 0, f"Expected no span_ids, got {span_ids}"
        assert len(trace_ids) == 0, f"Expected no trace_ids, got {trace_ids}"
