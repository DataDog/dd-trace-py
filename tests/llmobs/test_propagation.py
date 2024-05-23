import json
import os

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _inject_llmobs_parent_id
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import DummyTracer


def test_inject_llmobs_parent_id_no_llmobs_span():
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("Non-LLMObs span"):
        with dummy_tracer.trace("Non-LLMObs span") as child_span:
            _inject_llmobs_parent_id(child_span.context)
    assert child_span.context._meta.get(PROPAGATED_PARENT_ID_KEY, None) is None


def test_inject_llmobs_parent_id_simple():
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as root_span:
        _inject_llmobs_parent_id(root_span.context)
    assert root_span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(root_span.span_id)


def test_inject_llmobs_parent_id_nested_llmobs_non_llmobs():
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as root_span:
        with dummy_tracer.trace("Non-LLMObs span") as child_span:
            _inject_llmobs_parent_id(child_span.context)
    assert child_span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(root_span.span_id)


def test_inject_llmobs_parent_id_non_llmobs_root_span():
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("Non-LLMObs span"):
        with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as child_span:
            _inject_llmobs_parent_id(child_span.context)
    assert child_span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(child_span.span_id)


def test_inject_llmobs_parent_id_nested_llmobs_spans():
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM):
        with dummy_tracer.trace("LLMObs child span", span_type=SpanTypes.LLM):
            with dummy_tracer.trace("Last LLMObs child span", span_type=SpanTypes.LLM) as last_llmobs_span:
                _inject_llmobs_parent_id(last_llmobs_span.context)
    assert last_llmobs_span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(last_llmobs_span.span_id)


def test_propagate_correct_llmobs_parent_id_simple(run_python_code_in_subprocess):
    """Test that the correct LLMObs parent ID is propagated in the headers in a simple distributed scenario.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have the LLMObs parent ID from service A's root LLMObs span.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.propagation.http import HTTPPropagator

with tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        HTTPPropagator.inject(child_span.context, headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env["DD_LLMOBS_ENABLED"] = "1"
    env["DD_TRACE_ENABLED"] = "0"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    context = HTTPPropagator.extract(headers)
    dummy_tracer = DummyTracer()
    dummy_tracer.context_provider.activate(context)
    with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert _get_llmobs_parent_id(span) == headers["_DD_LLMOBS_SPAN_ID"]


def test_propagate_llmobs_parent_id_complex(run_python_code_in_subprocess):
    """Test that the correct LLMObs parent ID is propagated in the headers in a more complex trace.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a non-LLMObs local root span and a LLMObs child span.
    Both of service B's spans should have the same LLMObs parent ID (Root LLMObs span from service A).
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.propagation.http import HTTPPropagator

with tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        HTTPPropagator.inject(child_span.context, headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env["DD_LLMOBS_ENABLED"] = "1"
    env["DD_TRACE_ENABLED"] = "0"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    context = HTTPPropagator.extract(headers)
    dummy_tracer = DummyTracer()
    dummy_tracer.context_provider.activate(context)
    with dummy_tracer.trace("Non-LLMObs span") as span:
        with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as llm_span:
            assert str(span.parent_id) == headers["x-datadog-parent-id"]
            assert _get_llmobs_parent_id(span) == headers["_DD_LLMOBS_SPAN_ID"]
            assert _get_llmobs_parent_id(llm_span) == headers["_DD_LLMOBS_SPAN_ID"]


def test_no_llmobs_parent_id_propagated_if_no_llmobs_spans(run_python_code_in_subprocess):
    """Test that the correct LLMObs parent ID (None) is extracted from the headers in a simple distributed scenario.
    Service A (subprocess) has spans, but none are LLMObs spans.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have no LLMObs parent ID as there are no LLMObs spans from service A.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.propagation.http import HTTPPropagator

with tracer.trace("Non-LLMObs span") as root_span:
    headers = {}
    HTTPPropagator.inject(root_span.context, headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env["DD_LLMOBS_ENABLED"] = "1"
    env["DD_TRACE_ENABLED"] = "0"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    context = HTTPPropagator.extract(headers)
    dummy_tracer = DummyTracer()
    dummy_tracer.context_provider.activate(context)
    with dummy_tracer.trace("LLMObs span", span_type=SpanTypes.LLM) as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert _get_llmobs_parent_id(span) is None
