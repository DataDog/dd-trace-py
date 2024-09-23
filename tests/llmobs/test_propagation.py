import json
import os

import pytest

from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.contrib.internal.futures.patch import patch as patch_futures
from ddtrace.contrib.internal.futures.patch import unpatch as unpatch_futures


def test_inject_llmobs_parent_id_no_llmobs_span(LLMObs):
    request_headers = {}
    with LLMObs._instance.tracer.trace("Non-LLMObs span"):
        with LLMObs._instance.tracer.trace("Non-LLMObs span"):
            LLMObs._inject_llmobs_context(request_headers)
    assert request_headers.get(PARENT_ID_KEY) == ROOT_PARENT_ID


def test_inject_llmobs_parent_id_simple(LLMObs):
    request_headers = {}
    with LLMObs.workflow("LLMObs span") as root_span:
        LLMObs._inject_llmobs_context(request_headers)
    assert request_headers.get(PARENT_ID_KEY) == str(root_span.span_id)


def test_inject_llmobs_parent_id_nested_llmobs_non_llmobs(LLMObs):
    request_headers = {}
    with LLMObs.workflow("LLMObs span") as root_span:
        with LLMObs._instance.tracer.trace("Non-LLMObs span"):
            LLMObs._inject_llmobs_context(request_headers)
    assert request_headers.get(PARENT_ID_KEY) == str(root_span.span_id)


def test_inject_llmobs_parent_id_non_llmobs_root_span(LLMObs):
    request_headers = {}
    with LLMObs._instance.tracer.trace("Non-LLMObs span"):
        with LLMObs.workflow("LLMObs span") as child_span:
            LLMObs._inject_llmobs_context(request_headers)
    assert request_headers.get(PARENT_ID_KEY) == str(child_span.span_id)


def test_inject_llmobs_parent_id_nested_llmobs_spans(LLMObs):
    request_headers = {}
    with LLMObs.workflow("LLMObs span"):
        with LLMObs.workflow("LLMObs child span"):
            with LLMObs.workflow("Last LLMObs child span") as last_llmobs_span:
                LLMObs._inject_llmobs_context(request_headers)
    assert request_headers.get(PARENT_ID_KEY) == str(last_llmobs_span.span_id)


def test_propagate_correct_llmobs_parent_id_simple(ddtrace_run_python_code_in_subprocess, LLMObs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a simple distributed scenario.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have the LLMObs parent ID from service A's root LLMObs span.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs
from ddtrace.propagation.http import HTTPPropagator

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span"):
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert span.get_tag(PARENT_ID_KEY) == headers["_DD_LLMOBS_SPAN_ID"]


def test_propagate_llmobs_parent_id_complex(ddtrace_run_python_code_in_subprocess, LLMObs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a more complex trace.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a non-LLMObs local root span and a LLMObs child span.
    Both of service B's spans should have the same LLMObs parent ID (Root LLMObs span from service A).
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs
from ddtrace.propagation.http import HTTPPropagator

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span"):
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs._instance.tracer.trace("Non-LLMObs span") as span:
        with LLMObs.workflow("LLMObs span") as llm_span:
            assert str(span.parent_id) == headers["x-datadog-parent-id"]
            assert span.get_tag(PARENT_ID_KEY) is None
            assert llm_span.get_tag(PARENT_ID_KEY) == headers["_DD_LLMOBS_SPAN_ID"]


def test_no_llmobs_parent_id_propagated_if_no_llmobs_spans(ddtrace_run_python_code_in_subprocess, LLMObs):
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
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers.get("x-datadog-parent-id")
        assert span.get_tag(PARENT_ID_KEY) == ROOT_PARENT_ID


def test_inject_distributed_headers_simple(LLMObs):
    with LLMObs.workflow("LLMObs span") as root_span:
        request_headers = LLMObs.inject_distributed_headers({}, span=root_span)
    assert PARENT_ID_KEY in request_headers


def test_inject_distributed_headers_nested_llmobs_non_llmobs(LLMObs):
    with LLMObs.workflow("LLMObs span"):
        with LLMObs._instance.tracer.trace("Non-LLMObs span") as child_span:
            request_headers = LLMObs.inject_distributed_headers({}, span=child_span)
    assert PARENT_ID_KEY in request_headers


def test_inject_distributed_headers_non_llmobs_root_span(LLMObs):
    with LLMObs._instance.tracer.trace("Non-LLMObs span"):
        with LLMObs.workflow("LLMObs span") as child_span:
            request_headers = LLMObs.inject_distributed_headers({}, span=child_span)
    assert PARENT_ID_KEY in request_headers


def test_inject_distributed_headers_nested_llmobs_spans(LLMObs):
    with LLMObs.workflow("LLMObs span"):
        with LLMObs.workflow("LLMObs child span"):
            with LLMObs.workflow("LLMObs grandchild span") as last_llmobs_span:
                request_headers = LLMObs.inject_distributed_headers({}, span=last_llmobs_span)
    assert PARENT_ID_KEY in request_headers


def test_activate_distributed_headers_propagate_correct_llmobs_parent_id_simple(
    ddtrace_run_python_code_in_subprocess, LLMObs
):
    """Test that the correct LLMObs parent ID is propagated in the headers in a simple distributed scenario.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a LLMObs span.
    Service B's span should have the LLMObs parent ID from service A's root LLMObs span.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        headers = LLMObs.inject_distributed_headers(headers, span=child_span)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs.workflow("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert span.get_tag(PARENT_ID_KEY) == headers["_DD_LLMOBS_SPAN_ID"]


def test_activate_distributed_headers_propagate_llmobs_parent_id_complex(ddtrace_run_python_code_in_subprocess, LLMObs):
    """Test that the correct LLMObs parent ID is propagated in the headers in a more complex trace.
    Service A (subprocess) has a root LLMObs span and a non-LLMObs child span.
    Service B (outside subprocess) has a non-LLMObs local root span and a LLMObs child span.
    Service B's LLMObs span should have the  LLMObs parent ID (Root LLMObs span from service A).
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs

with LLMObs.workflow("LLMObs span") as root_span:
    with tracer.trace("Non-LLMObs span") as child_span:
        headers = {"_DD_LLMOBS_SPAN_ID": str(root_span.span_id)}
        headers = LLMObs.inject_distributed_headers(headers)

print(json.dumps(headers))
        """
    env = os.environ.copy()
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs._instance.tracer.trace("Non-LLMObs span") as span:
        with LLMObs.llm(model_name="llm_model", name="LLMObs span") as llm_span:
            assert str(span.parent_id) == headers["x-datadog-parent-id"]
            assert span.get_tag(PARENT_ID_KEY) is None
            assert llm_span.get_tag(PARENT_ID_KEY) == headers["_DD_LLMOBS_SPAN_ID"]


def test_activate_distributed_headers_does_not_propagate_if_no_llmobs_spans(
    ddtrace_run_python_code_in_subprocess, LLMObs
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
    env.update({"DD_LLMOBS_ML_APP": "test-app", "DD_LLMOBS_ENABLED": "1"})
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    headers = json.loads(stdout.decode())
    LLMObs.activate_distributed_headers(headers)
    with LLMObs.task("LLMObs span") as span:
        assert str(span.parent_id) == headers["x-datadog-parent-id"]
        assert span.get_tag(PARENT_ID_KEY) == ROOT_PARENT_ID


@pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_ml_app="test_app_name")])
def test_threading_submit_propagation(LLMObs, mock_llmobs_span_writer, ddtrace_global_config):
    # Assert the threading patch propagates the llmobs tracing context if llmobs is enabled.
    patch_futures()
    import concurrent.futures

    def fn():
        with LLMObs.task("executor.thread"):
            return 42

    with LLMObs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fn)
            result = future.result()
            assert result == 42
    assert mock_llmobs_span_writer.enqueue.call_count == 2
    main_thread_span, executor_thread_span = None, None
    for span in mock_llmobs_span_writer.enqueue.call_args_list:
        if span[0][0]["name"] == "main.thread":
            main_thread_span = span[0][0]
        else:
            executor_thread_span = span[0][0]
    assert main_thread_span["parent_id"] == ROOT_PARENT_ID
    assert executor_thread_span["parent_id"] == main_thread_span["span_id"]
    unpatch_futures()


@pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_ml_app="test_app_name")])
def test_threading_map_propagation(LLMObs, mock_llmobs_span_writer, ddtrace_global_config):
    # Assert the threading patch propagates the llmobs tracing context if llmobs is enabled.
    patch_futures()
    import concurrent.futures

    def fn(value):
        with LLMObs.task("executor.thread"):
            return value

    with LLMObs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            _ = executor.map(fn, (1, 2))
    assert mock_llmobs_span_writer.enqueue.call_count == 3
    main_thread_span = None
    executor_thread_spans = []
    for span in mock_llmobs_span_writer.enqueue.call_args_list:
        if span[0][0]["name"] == "main.thread":
            main_thread_span = span[0][0]
        else:
            executor_thread_spans.append(span[0][0])
    assert main_thread_span["parent_id"] == ROOT_PARENT_ID
    assert executor_thread_spans[0]["parent_id"] == main_thread_span["span_id"]
    assert executor_thread_spans[1]["parent_id"] == main_thread_span["span_id"]
    unpatch_futures()
