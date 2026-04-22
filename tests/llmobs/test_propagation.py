import json
import os

import pytest

from ddtrace.contrib.internal.asyncio.patch import patch as patch_asyncio
from ddtrace.contrib.internal.asyncio.patch import unpatch as unpatch_asyncio
from ddtrace.contrib.internal.futures.patch import patch as patch_futures
from ddtrace.contrib.internal.futures.patch import unpatch as unpatch_futures
from ddtrace.llmobs._constants import PROPAGATED_LLMOBS_TRACE_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_trace_id


@pytest.fixture
def patched_futures():
    patch_futures()
    yield
    unpatch_futures()


@pytest.fixture
def patched_asyncio():
    patch_asyncio()
    yield
    unpatch_asyncio()


def test_inject_llmobs_parent_id_no_llmobs_span(llmobs):
    with llmobs._instance.tracer.trace("Non-LLMObs span"):
        with llmobs._instance.tracer.trace("Non-LLMObs span") as span:
            llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == ROOT_PARENT_ID


def test_inject_llmobs_parent_id_simple(llmobs):
    with llmobs.workflow("LLMObs span") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(span.span_id)
    assert span.context._meta.get(PROPAGATED_ML_APP_KEY) == "unnamed-ml-app"


def test_inject_llmobs_ml_app_override(llmobs):
    with llmobs.workflow(name="LLMObs span", ml_app="test-ml-app") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_ML_APP_KEY) == "test-ml-app"


def test_inject_llmobs_parent_id_nested_llmobs_non_llmobs(llmobs):
    with llmobs.workflow("LLMObs span") as root_span:
        with llmobs._instance.tracer.trace("Non-LLMObs span") as span:
            llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(root_span.span_id)


def test_inject_llmobs_parent_id_non_llmobs_root_span(llmobs):
    with llmobs._instance.tracer.trace("Non-LLMObs span"):
        with llmobs.workflow("LLMObs span") as span:
            llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(span.span_id)


def test_inject_llmobs_parent_id_nested_llmobs_spans(llmobs):
    with llmobs.workflow("LLMObs span"):
        with llmobs.workflow("LLMObs child span"):
            with llmobs.workflow("Last LLMObs child span") as last_llmobs_span:
                llmobs._inject_llmobs_context(last_llmobs_span.context, {})
    assert last_llmobs_span.context._meta.get(PROPAGATED_PARENT_ID_KEY) == str(last_llmobs_span.span_id)


def test_propagate_correct_llmobs_parent_id_simple(ddtrace_run_python_code_in_subprocess, llmobs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a simple distributed scenario.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have the LLMObs parent ID from service A's root LLMObs span.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs
from ddtrace.propagation.http import HTTPPropagator

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span"):
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1", "DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert get_llmobs_parent_id(span) == headers["_DD_LLMOBS_SPAN_ID"]


def test_propagate_llmobs_parent_id_complex(ddtrace_run_python_code_in_subprocess, llmobs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a more complex trace.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a non-LLMObs local root span and a LLMObs child span.
    Both of service B's spans should have the same LLMObs parent ID (Root LLMObs span from service A).
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs
from ddtrace.propagation.http import HTTPPropagator

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span"):
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1", "DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs._instance.tracer.trace("Non-LLMObs span") as span:
        with llmobs.llm(model_name="llm_model", name="LLMObs span") as llm_span:
            assert str(span.parent_id) == headers["x-datadog-parent-id"]
            assert get_llmobs_parent_id(span) is None
            assert get_llmobs_parent_id(llm_span) == headers["_DD_LLMOBS_SPAN_ID"]


def test_no_llmobs_parent_id_propagated_if_no_llmobs_spans(ddtrace_run_python_code_in_subprocess, llmobs):
    """Test that the correct LLMObs parent ID (None) is extracted from the headers in a simple distributed scenario.
    Service A (subprocess) has spans, but none are LLMObs spans.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have no LLMObs parent ID as there are no LLMObs spans from service A.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs
from ddtrace.propagation.http import HTTPPropagator

with tracer.trace("Non-LLMObs span") as span:
    headers = {}
    LLMObs.inject_distributed_headers(headers)
    HTTPPropagator.inject(span.context, headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1", "DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers.get("x-datadog-parent-id")
        assert get_llmobs_parent_id(span) == ROOT_PARENT_ID


def test_inject_distributed_headers_simple(llmobs):
    with llmobs.workflow("LLMObs span") as root_span:
        request_headers = llmobs.inject_distributed_headers({}, span=root_span)
    assert "llmobs_parent_id:{}".format(root_span.span_id) in request_headers.get("tracestate")


def test_inject_distributed_headers_nested_llmobs_non_llmobs(llmobs):
    with llmobs.workflow("LLMObs span") as root_span:
        with llmobs._instance.tracer.trace("Non-LLMObs span") as child_span:
            request_headers = llmobs.inject_distributed_headers({}, span=child_span)
    assert "llmobs_parent_id:{}".format(root_span.span_id) in request_headers.get("tracestate")


def test_inject_distributed_headers_non_llmobs_root_span(llmobs):
    with llmobs._instance.tracer.trace("Non-LLMObs span"):
        with llmobs.workflow("LLMObs span") as child_span:
            request_headers = llmobs.inject_distributed_headers({}, span=child_span)
    assert "llmobs_parent_id:{}".format(child_span.span_id) in request_headers.get("tracestate")


def test_inject_distributed_headers_nested_llmobs_spans(llmobs):
    with llmobs.workflow("LLMObs span"):
        with llmobs.workflow("LLMObs child span"):
            with llmobs.workflow("LLMObs grandchild span") as last_llmobs_span:
                request_headers = llmobs.inject_distributed_headers({}, span=last_llmobs_span)
    assert "llmobs_parent_id:{}".format(last_llmobs_span.span_id) in request_headers.get("tracestate")


def test_activate_distributed_headers_propagate_simple(ddtrace_run_python_code_in_subprocess, llmobs_no_ml_app):
    """Test that the correct LLMObs parent ID is propagated in the headers in a simple distributed scenario.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have the LLMObs parent ID from service A's root LLMObs span.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import get_llmobs_trace_id

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {
            "_DD_LLMOBS_SPAN_ID": str(root_span.span_id),
            "_DD_LLMOBS_TRACE_ID": get_llmobs_trace_id(root_span)
        }
        headers = LLMObs.inject_distributed_headers(headers, span=child_span)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs_no_ml_app.activate_distributed_headers(headers)
    with llmobs_no_ml_app.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert get_llmobs_parent_id(span) == headers["_DD_LLMOBS_SPAN_ID"]
        assert get_llmobs_ml_app(span) == "test-app"  # should have been propagated
        assert get_llmobs_trace_id(span) == headers["_DD_LLMOBS_TRACE_ID"]


def test_activate_distributed_headers_propagate_complex(ddtrace_run_python_code_in_subprocess, llmobs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a more complex trace.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a non-LLMObs local root span and a LLMObs child span.
    Service B's LLMObs span should have the  LLMObs parent ID (Root LLMObs span from service A).
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        headers = LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs._instance.tracer.trace("Non-LLMObs span") as span:
        with llmobs.llm(model_name="llm_model", name="LLMObs span") as llm_span:
            assert str(span.parent_id) == headers["x-datadog-parent-id"]
            assert get_llmobs_parent_id(span) is None
            assert get_llmobs_parent_id(llm_span) == headers["_DD_LLMOBS_SPAN_ID"]
            assert get_llmobs_ml_app(llm_span) == "test-app"  # should be the one set by `llmobs` fixture


def test_activate_distributed_headers_for_local_ml_app(ddtrace_run_python_code_in_subprocess, llmobs):
    """
    Tests that the local ML app is used when injecting distributed headers from service A, and that
    service B's span uses that ML app from the propagated headers.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow(name="LLMObs span", ml_app="local-ml-app") as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = LLMObs.inject_distributed_headers({}, span=child_span)

print(json.dumps(headers))
"""

    env = os.environ.copy()
    env.update({"DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs.llm(name="llm_model") as llm_span:
        assert get_llmobs_ml_app(llm_span) == "local-ml-app"


def test_activate_distributed_headers_for_twice_propagated_ml_app(ddtrace_run_python_code_in_subprocess, llmobs):
    """
    Tests that if there is a propagate ML app in service A from some other unknown service, that
    the value of that propagated ML app is used when injecting distributed headers from service A.
    Service B's span should use the ML app from the propagated headers.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with tracer.trace("Non-LLMObs span") as root_span:
    root_span.context._meta["_dd.p.llmobs_ml_app"] = "twice-propagated-ml-app"
    with LLMObs.workflow(name="LLMObs span") as llmobs_span:
        headers = LLMObs.inject_distributed_headers({}, span=root_span)

print(json.dumps(headers))
"""

    env = os.environ.copy()
    env.update({"DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())

    llmobs.activate_distributed_headers(headers)
    with llmobs.llm(name="llm_model") as llm_span:
        assert get_llmobs_ml_app(llm_span) == "twice-propagated-ml-app"


def test_activate_distributed_headers_does_not_propagate_if_no_llmobs_spans(
    ddtrace_run_python_code_in_subprocess, llmobs
):
    """Test that the correct LLMObs parent ID (None) is extracted from the headers in a simple distributed scenario.
    Service A (subprocess) has spans, but none are LLMObs spans.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have no LLMObs parent ID as there are no LLMObs spans from service A.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

with tracer.trace("Non-LLMObs span") as root_span:
    headers = {}
    headers = LLMObs.inject_distributed_headers(headers, span=root_span)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1", "DD_TRACE_ENABLED": "0"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    llmobs.activate_distributed_headers(headers)
    with llmobs.task("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert get_llmobs_parent_id(span) == ROOT_PARENT_ID
        assert get_llmobs_ml_app(span) == "test-app"


def test_threading_submit_propagation(llmobs, llmobs_events, patched_futures):
    import concurrent.futures

    def fn():
        with llmobs.task("executor.thread"):
            return 42

    with llmobs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fn)
            result = future.result()
            assert result == 42
    assert len(llmobs_events) == 2
    main_thread_span, executor_thread_span = None, None
    for span in llmobs_events:
        if span["name"] == "main.thread":
            main_thread_span = span
        else:
            executor_thread_span = span
    assert main_thread_span["parent_id"] == ROOT_PARENT_ID
    assert executor_thread_span["parent_id"] == main_thread_span["span_id"]
    assert main_thread_span["trace_id"] == executor_thread_span["trace_id"]
    assert main_thread_span["_dd"]["apm_trace_id"] == executor_thread_span["_dd"]["apm_trace_id"]
    assert main_thread_span["trace_id"] != main_thread_span["_dd"]["apm_trace_id"]


def test_threading_map_propagation(llmobs, llmobs_events, patched_futures):
    import concurrent.futures

    def fn(value):
        with llmobs.task("executor.thread"):
            return value

    with llmobs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            _ = executor.map(fn, (1, 2))
    assert len(llmobs_events) == 3
    main_thread_span = None
    executor_thread_spans = []
    for span in llmobs_events:
        if span["name"] == "main.thread":
            main_thread_span = span
        else:
            executor_thread_spans.append(span)
    assert main_thread_span["parent_id"] == ROOT_PARENT_ID
    assert executor_thread_spans[0]["parent_id"] == main_thread_span["span_id"]
    assert executor_thread_spans[1]["parent_id"] == main_thread_span["span_id"]
    assert main_thread_span["trace_id"] == executor_thread_spans[0]["trace_id"]
    assert main_thread_span["trace_id"] == executor_thread_spans[1]["trace_id"]
    assert main_thread_span["_dd"]["apm_trace_id"] == executor_thread_spans[0]["_dd"]["apm_trace_id"]
    assert main_thread_span["_dd"]["apm_trace_id"] == executor_thread_spans[1]["_dd"]["apm_trace_id"]
    assert main_thread_span["trace_id"] != main_thread_span["_dd"]["apm_trace_id"]


async def test_asyncio_create_task(llmobs, llmobs_events, patched_asyncio):
    import asyncio

    async def fn():
        with llmobs.task("side_task"):
            return 42

    with llmobs.workflow("main_task"):
        asyncio.create_task(fn())

    await asyncio.sleep(0)  # Need this to allow fn() task to run

    main_task_span, side_task_span = None, None
    for span in llmobs_events:
        if span["name"] == "main_task":
            main_task_span = span
        else:
            side_task_span = span
    assert main_task_span["parent_id"] == ROOT_PARENT_ID
    assert side_task_span["parent_id"] == main_task_span["span_id"]
    assert main_task_span["trace_id"] == side_task_span["trace_id"]
    assert main_task_span["_dd"]["apm_trace_id"] == side_task_span["_dd"]["apm_trace_id"]
    assert main_task_span["trace_id"] != main_task_span["_dd"]["apm_trace_id"]


_HEX_TRACE_ID = "ef017ddb6db557ea44fb6ce732fd0687"
_DECIMAL_TRACE_ID = str(int(_HEX_TRACE_ID, 16))


def _make_upstream_llmobs_context(trace_id_value, parent_id="987654321"):
    from ddtrace.trace import Context

    ctx = Context(trace_id=123456789, span_id=int(parent_id))
    ctx._meta[PROPAGATED_LLMOBS_TRACE_ID_KEY] = trace_id_value
    ctx._meta[PROPAGATED_PARENT_ID_KEY] = parent_id
    return ctx


def test_injected_trace_id_is_decimal_on_the_wire(llmobs):
    """Wire must stay decimal so older SDKs that do int(header) keep working."""
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    wire_value = span.context._meta.get(PROPAGATED_LLMOBS_TRACE_ID_KEY)
    assert wire_value and wire_value.isdigit()


def test_activate_decimal_header_stores_canonical_hex(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        assert get_llmobs_trace_id(span) == _HEX_TRACE_ID


def test_activate_hex_header_stores_canonical_hex(llmobs):
    ctx = _make_upstream_llmobs_context(_HEX_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        assert get_llmobs_trace_id(span) == _HEX_TRACE_ID


def test_activate_non_numeric_header_passthrough_does_not_raise(llmobs):
    custom_tid = "my-custom-trace-id-not-numeric"
    ctx = _make_upstream_llmobs_context(custom_tid)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        assert get_llmobs_trace_id(span) == custom_tid


def test_submitted_event_trace_id_is_hex_when_upstream_sent_decimal(llmobs, llmobs_events):
    """Backend must always see hex so trace joining works even across mixed-version upstreams."""
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["trace_id"] == _HEX_TRACE_ID


def test_inject_skips_header_when_trace_id_is_empty(llmobs, monkeypatch):
    monkeypatch.setattr("ddtrace.llmobs._llmobs._trace_id_to_wire", lambda _v: None)
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_LLMOBS_TRACE_ID_KEY) != ""
