# -*- coding: utf-8 -*-
import os

import pytest

from tests.integration.utils import AGENT_VERSION
from tests.utils import snapshot


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")

# Fields that vary between runs and must be excluded from snapshot comparison.
_IGNORES = [
    "meta.runtime-id",
    "meta._dd.p.tid",
    "metrics.process_id",
]


def _nsw_env():
    """Return a copy of the environment with NativeSpanWriter enabled.

    The snapshot context (set up by @snapshot before the test body runs) writes
    X-Datadog-Test-Session-Token into _DD_TRACE_WRITER_ADDITIONAL_HEADERS in
    os.environ.  Copying os.environ here — inside the test body — ensures the
    subprocess inherits that header so its traces land in the right snapshot
    session.
    """
    env = os.environ.copy()
    env["_DD_TRACE_NATIVE_SPAN_WRITER_ENABLED"] = "1"
    return env


@snapshot(ignores=_IGNORES)
def test_native_span_writer_single_trace(run_python_code_in_subprocess):
    """NativeSpanWriter sends a basic trace end-to-end."""
    code = """
from ddtrace.trace import tracer

with tracer.trace("op", service="my-svc", resource="res") as span:
    span.set_tag("k", "v")
    span._set_attribute("num", 42.0)
tracer.flush()
tracer.shutdown()
"""
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=_nsw_env())
    assert status == 0, stderr.decode()


@snapshot(ignores=_IGNORES)
def test_native_span_writer_multiple_traces(run_python_code_in_subprocess):
    """NativeSpanWriter sends multiple traces correctly."""
    code = """
from ddtrace.trace import tracer

with tracer.trace("op1", service="svc") as s:
    s.set_tag("key", "val1")
    with tracer.trace("child1"):
        pass

with tracer.trace("op2", service="svc") as s:
    s.set_tag("key", "val2")
    with tracer.trace("child2"):
        pass

tracer.flush()
tracer.shutdown()
"""
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=_nsw_env())
    assert status == 0, stderr.decode()


@snapshot(ignores=_IGNORES)
def test_native_span_writer_meta_struct(run_python_code_in_subprocess):
    """NativeSpanWriter encodes meta_struct values correctly at flush time."""
    code = """
from ddtrace.trace import tracer

with tracer.trace("op", service="svc") as span:
    span._set_struct_tag("payload", {"key": "value", "count": 1})
tracer.flush()
tracer.shutdown()
"""
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=_nsw_env())
    assert status == 0, stderr.decode()
