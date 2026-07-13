import json
import logging
import os

import pytest

from ddtrace.contrib.internal.asyncio.patch import patch as patch_asyncio
from ddtrace.contrib.internal.asyncio.patch import unpatch as unpatch_asyncio
from ddtrace.contrib.internal.futures.patch import patch as patch_futures
from ddtrace.contrib.internal.futures.patch import unpatch as unpatch_futures
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import PROPAGATED_LLMOBS_TRACE_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_AGENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_AGENT_NAME_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_SAMPLE_RATE
from ddtrace.llmobs._constants import PROPAGATED_SAMPLING_DECISION
from ddtrace.llmobs._constants import PROPAGATED_SESSION_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import LLMObsSamplingDecision
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_sample_rate
from ddtrace.llmobs._utils import get_llmobs_sampling_decision
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.trace import Context


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


def test_inject_llmobs_agent_service_uses_ml_app_key(llmobs):
    with llmobs.workflow(name="LLMObs span", agent_service="test-agent-service") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_ML_APP_KEY) == "test-agent-service"
    assert "_dd.p.llmobs_agent_service" not in span.context._meta


def test_inject_llmobs_session_id(llmobs):
    with llmobs.workflow(name="LLMObs span", session_id="test-session-id") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SESSION_ID_KEY) == "test-session-id"


def test_inject_llmobs_no_session_id(llmobs):
    with llmobs.workflow(name="LLMObs span") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SESSION_ID_KEY) is None


def test_session_id_trace_default_fills_sibling_gap(llmobs):
    """The first session set in a trace becomes the default; a sibling under a session-less parent inherits it."""
    with llmobs.workflow(name="root"):  # no session on the root
        with llmobs.llm(name="first", session_id="trace-session") as first:
            pass
        with llmobs.llm(name="second") as second:  # sibling, no explicit session
            pass
    assert get_llmobs_session_id(first) == "trace-session"
    assert get_llmobs_session_id(second) == "trace-session"


def test_session_id_local_override_beats_trace_default(llmobs):
    """An explicit session_id overrides the trace default locally; gap spans still get the default."""
    with llmobs.workflow(name="root", session_id="trace-session"):
        with llmobs.llm(name="override", session_id="other-session") as override_span:
            pass
        with llmobs.llm(name="default") as default_span:  # no explicit session
            pass
    assert get_llmobs_session_id(override_span) == "other-session"
    assert get_llmobs_session_id(default_span) == "trace-session"


def test_injection_propagates_trace_default_session_not_override(llmobs):
    """Injecting while an override child is active propagates the trace default, not the override,
    and does not corrupt the trace default for later spans.
    """
    with llmobs.workflow(name="root"):  # no session on the root
        with llmobs.llm(name="first", session_id="trace-session"):  # establishes the trace default
            pass
        with llmobs.llm(name="override", session_id="override-session") as override_span:
            headers = llmobs.inject_distributed_headers({}, span=override_span)
        with llmobs.llm(name="later") as later_span:  # sibling created after the override
            pass
    tags = headers.get("x-datadog-tags", "")
    assert "_dd.p.llmobs_sid=trace-session" in tags
    assert "_dd.p.llmobs_sid=override-session" not in tags
    # the override must not have replaced the trace default for later spans
    assert get_llmobs_session_id(later_span) == "trace-session"


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


def test_activate_distributed_headers_for_session_id(ddtrace_run_python_code_in_subprocess, llmobs):
    """A session_id set on service A's root LLMObs span is inherited by service B's LLMObs child span."""
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow(name="LLMObs span", session_id="session-from-service-a") as root_span:
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
        assert get_llmobs_session_id(llm_span) == "session-from-service-a"


def test_activate_distributed_headers_local_session_id_wins(ddtrace_run_python_code_in_subprocess, llmobs):
    """An explicit session_id on service B's span overrides the session propagated from service A."""
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow(name="LLMObs span", session_id="session-from-service-a") as root_span:
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
    with llmobs.llm(name="llm_model", session_id="local-session") as llm_span:
        assert get_llmobs_session_id(llm_span) == "local-session"


def test_activate_distributed_headers_propagates_trace_default_over_override(
    ddtrace_run_python_code_in_subprocess, llmobs
):
    """When an override child is active at injection time, service B inherits the trace default
    (the first session set in the trace), not the active override.
    """
    code = """
import json

from ddtrace import tracer
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="test-app", site="datad0g.com", api_key="dummy-key", agentless_enabled=True)

with LLMObs.workflow(name="root"):  # no session; the first child sets the trace default
    with LLMObs.llm(name="first", session_id="trace-session"):
        pass
    with LLMObs.llm(name="override", session_id="override-session"):
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
        assert get_llmobs_session_id(llm_span) == "trace-session"


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


def test_threading_submit_propagation(llmobs, test_spans, patched_futures):
    import concurrent.futures

    def fn():
        with llmobs.task("executor.thread"):
            return 42

    with llmobs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fn)
            result = future.result()
            assert result == 42
    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 2
    main_thread_span, executor_thread_span = None, None
    for span in spans:
        if span.name == "main.thread":
            main_thread_span = span
        else:
            executor_thread_span = span
    main_apm_trace_id = format_trace_id(main_thread_span.trace_id)
    assert get_llmobs_parent_id(main_thread_span) == ROOT_PARENT_ID
    assert get_llmobs_parent_id(executor_thread_span) == str(main_thread_span.span_id)
    assert get_llmobs_trace_id(main_thread_span) == get_llmobs_trace_id(executor_thread_span)
    assert main_apm_trace_id == format_trace_id(executor_thread_span.trace_id)
    assert get_llmobs_trace_id(main_thread_span) != main_apm_trace_id


def test_threading_map_propagation(llmobs, test_spans, patched_futures):
    import concurrent.futures

    def fn(value):
        with llmobs.task("executor.thread"):
            return value

    with llmobs.workflow("main.thread"):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            _ = executor.map(fn, (1, 2))
    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 3
    main_thread_span = None
    executor_thread_spans = []
    for span in spans:
        if span.name == "main.thread":
            main_thread_span = span
        else:
            executor_thread_spans.append(span)
    main_apm_trace_id = format_trace_id(main_thread_span.trace_id)
    main_trace_id = get_llmobs_trace_id(main_thread_span)
    assert get_llmobs_parent_id(main_thread_span) == ROOT_PARENT_ID
    assert get_llmobs_parent_id(executor_thread_spans[0]) == str(main_thread_span.span_id)
    assert get_llmobs_parent_id(executor_thread_spans[1]) == str(main_thread_span.span_id)
    assert main_trace_id == get_llmobs_trace_id(executor_thread_spans[0])
    assert main_trace_id == get_llmobs_trace_id(executor_thread_spans[1])
    assert main_apm_trace_id == format_trace_id(executor_thread_spans[0].trace_id)
    assert main_apm_trace_id == format_trace_id(executor_thread_spans[1].trace_id)
    assert main_trace_id != main_apm_trace_id


async def test_asyncio_create_task(llmobs, test_spans, patched_asyncio):
    import asyncio

    async def fn():
        with llmobs.task("side_task"):
            return 42

    with llmobs.workflow("main_task"):
        asyncio.create_task(fn())

    await asyncio.sleep(0)  # Need this to allow fn() task to run

    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    main_task_span, side_task_span = None, None
    for span in spans:
        if span.name == "main_task":
            main_task_span = span
        else:
            side_task_span = span
    main_apm_trace_id = format_trace_id(main_task_span.trace_id)
    assert get_llmobs_parent_id(main_task_span) == ROOT_PARENT_ID
    assert get_llmobs_parent_id(side_task_span) == str(main_task_span.span_id)
    assert get_llmobs_trace_id(main_task_span) == get_llmobs_trace_id(side_task_span)
    assert main_apm_trace_id == format_trace_id(side_task_span.trace_id)
    assert get_llmobs_trace_id(main_task_span) != main_apm_trace_id


_HEX_TRACE_ID = "ef017ddb6db557ea44fb6ce732fd0687"
_DECIMAL_TRACE_ID = str(int(_HEX_TRACE_ID, 16))


def _make_upstream_llmobs_context(trace_id_value, parent_id="987654321"):
    ctx = Context(trace_id=123456789, span_id=int(parent_id))
    ctx._meta[PROPAGATED_LLMOBS_TRACE_ID_KEY] = trace_id_value
    ctx._meta[PROPAGATED_PARENT_ID_KEY] = parent_id
    return ctx


def test_current_trace_context_includes_parent_id(llmobs):
    """_current_trace_context must set PROPAGATED_PARENT_ID_KEY so that HTTPPropagator.inject
    (and any other consumer that reads _meta directly) propagates _dd.p.llmobs_parent_id to
    subprocess / HTTP consumers. Without this, dd-trace-go's contextWithPropagatedLLMSpan
    sees an empty parent_id and falls through to newLLMObsTraceID(), orphaning the child trace.
    """
    with llmobs.workflow("w") as span:
        ctx = llmobs._instance._current_trace_context()
    assert ctx is not None
    assert ctx._meta.get(PROPAGATED_PARENT_ID_KEY) == str(span.span_id)
    assert ctx._meta.get(PROPAGATED_LLMOBS_TRACE_ID_KEY) is not None


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


def test_submitted_event_trace_id_is_hex_when_upstream_sent_decimal(llmobs, test_spans):
    """Backend must always see hex so trace joining works even across mixed-version upstreams."""
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w"):
        pass
    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 1
    assert get_llmobs_trace_id(spans[0]) == _HEX_TRACE_ID


def test_inject_skips_header_when_trace_id_is_empty(llmobs, monkeypatch):
    monkeypatch.setattr("ddtrace.llmobs._llmobs._trace_id_to_wire", lambda _v: None)
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
        assert PROPAGATED_LLMOBS_TRACE_ID_KEY not in span.context._meta


# Ambiguous trace_id class: 32-char hex composed only of [0-9] with no leading zero.
# Indistinguishable by shape from a 32-digit decimal, which is why
# _normalize_wire_trace_id_to_hex is non-idempotent on these values and must only ever be
# called on wire-sourced inputs. The wire-decimal form is the int's decimal representation
# (~38 digits for 128-bit), so wire-in/hex-out round-trips cleanly.
_AMBIGUOUS_HEX_TRACE_ID = "12345678901234567890123456789012"
_AMBIGUOUS_WIRE_TRACE_ID = str(int(_AMBIGUOUS_HEX_TRACE_ID, 16))


def test_submitted_event_trace_id_matches_stored_for_ambiguous_hex(llmobs, test_spans):
    """Regression: submit must not re-normalize the stored hex. A trace_id whose 32-char hex
    form is all-[0-9] with no leading zero would otherwise be mangled by
    _normalize_wire_trace_id_to_hex reinterpreting it as decimal.
    """
    ctx = _make_upstream_llmobs_context(_AMBIGUOUS_WIRE_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        stored = get_llmobs_trace_id(span)
    assert stored == _AMBIGUOUS_HEX_TRACE_ID
    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 1
    assert get_llmobs_trace_id(spans[0]) == _AMBIGUOUS_HEX_TRACE_ID
    assert get_llmobs_trace_id(spans[0]) == stored


def test_inject_includes_sample_rate(llmobs):
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SAMPLE_RATE) == "1"


def test_inject_forwards_propagated_sample_rate_from_span(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SAMPLE_RATE) == "0.5"


def test_inject_forwards_propagated_sample_rate_from_context(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.25"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs._instance.tracer.trace("non-llmobs") as apm_span:
        llmobs._inject_llmobs_context(apm_span.context, {})
    assert apm_span.context._meta.get(PROPAGATED_SAMPLE_RATE) == "0.25"


def test_activate_distributed_context_stores_propagated_sample_rate(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    active_ctx = llmobs._instance._llmobs_context_provider.active()
    assert active_ctx._meta.get(PROPAGATED_SAMPLE_RATE) == "0.5"


def test_activate_distributed_context_without_sample_rate(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    active_ctx = llmobs._instance._llmobs_context_provider.active()
    assert PROPAGATED_SAMPLE_RATE not in active_ctx._meta


def test_activate_distributed_context_root_sentinel_no_warning(llmobs, caplog):
    ctx = Context(trace_id=123456789, span_id=987654321)
    ctx._meta[PROPAGATED_LLMOBS_TRACE_ID_KEY] = _DECIMAL_TRACE_ID
    ctx._meta[PROPAGATED_PARENT_ID_KEY] = ROOT_PARENT_ID
    with caplog.at_level(logging.DEBUG, logger="ddtrace.llmobs._llmobs"):
        llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    assert "Failed to parse LLMObs parent ID from request headers." not in caplog.text


def test_activate_distributed_context_garbage_parent_id_still_warns(llmobs, caplog):
    ctx = Context(trace_id=123456789, span_id=987654321)
    ctx._meta[PROPAGATED_LLMOBS_TRACE_ID_KEY] = _DECIMAL_TRACE_ID
    ctx._meta[PROPAGATED_PARENT_ID_KEY] = "not-a-number"
    with caplog.at_level(logging.WARNING, logger="ddtrace.llmobs._llmobs"):
        llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    assert "Failed to parse LLMObs parent ID from request headers." in caplog.text


def test_propagated_sample_rate_stored_in_meta_struct(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        assert get_llmobs_sample_rate(span) == "0.5"


def test_propagated_sample_rate_inherited_by_child_span(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("root"):
        with llmobs.workflow("child") as child:
            assert get_llmobs_sample_rate(child) == "0.5"


def test_sampling_decision_uses_propagated_rate(llmobs, llmobs_events):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.SAMPLED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["_dd"]["sample_rate"] == "0.5"
    assert llmobs_events[0]["_dd"]["sampling_decision"] == LLMObsSamplingDecision.SAMPLED


def test_no_sampling_decision_when_upstream_context_has_no_tags(llmobs, llmobs_events):
    # Upstream LLMObs context present but no sampling tags — the root service did not make
    # a decision, so we do not fabricate one. The span event carries no sampling fields.
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w"):
        pass
    assert len(llmobs_events) == 1
    assert "sample_rate" not in llmobs_events[0]["_dd"]
    assert "sampling_decision" not in llmobs_events[0]["_dd"]


def test_sampling_decision_defaults_to_sampled_for_root_span(llmobs, llmobs_events):
    # Root span with no LLMObs parent and no propagated context always samples.
    with llmobs.workflow("w"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["_dd"]["sample_rate"] == "1"
    assert llmobs_events[0]["_dd"]["sampling_decision"] == LLMObsSamplingDecision.SAMPLED


def test_inject_includes_sampling_decision(llmobs):
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SAMPLING_DECISION) == LLMObsSamplingDecision.SAMPLED


def test_inject_propagates_dropped_decision(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.DROPPED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        llmobs._inject_llmobs_context(span.context, {})
    assert span.context._meta.get(PROPAGATED_SAMPLING_DECISION) == LLMObsSamplingDecision.DROPPED


def test_activate_distributed_context_stores_propagated_sampling_decision(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.DROPPED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    active_ctx = llmobs._instance._llmobs_context_provider.active()
    assert active_ctx._meta.get(PROPAGATED_SAMPLING_DECISION) == LLMObsSamplingDecision.DROPPED


def test_propagated_sampling_decision_stored_in_meta_struct(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.DROPPED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w") as span:
        assert get_llmobs_sampling_decision(span) == LLMObsSamplingDecision.DROPPED


def test_propagated_sampling_decision_inherited_by_child_span(llmobs):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.DROPPED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("root"):
        with llmobs.workflow("child") as child:
            assert get_llmobs_sampling_decision(child) == LLMObsSamplingDecision.DROPPED


def test_propagated_sampling_decision_in_span_event(llmobs, llmobs_events):
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_SAMPLE_RATE] = "0.5"
    ctx._meta[PROPAGATED_SAMPLING_DECISION] = LLMObsSamplingDecision.DROPPED
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.workflow("w"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["_dd"]["sampling_decision"] == LLMObsSamplingDecision.DROPPED


# --- Agent attribution propagation ---------------------------------------------------------


def test_inject_agent_attribution_under_agent(llmobs):
    """Injecting from within an agent propagates the agent's id and name."""
    with llmobs.agent(name="my_agent") as agent_span:
        ctx = Context(trace_id=1, span_id=2)
        llmobs._inject_llmobs_context(ctx, {})
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) == str(agent_span.span_id)
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY) == "my_agent"


def test_inject_agent_attribution_inherited_through_tool(llmobs):
    """Injecting from a tool under an agent propagates the agent (inherited attribution)."""
    with llmobs.agent(name="my_agent") as agent_span:
        with llmobs.tool(name="my_tool"):
            ctx = Context(trace_id=1, span_id=2)
            llmobs._inject_llmobs_context(ctx, {})
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) == str(agent_span.span_id)
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY) == "my_agent"


def test_inject_no_agent_attribution_without_agent(llmobs):
    """No agent in the chain: neither agent key is written."""
    with llmobs.workflow("w"):
        ctx = Context(trace_id=1, span_id=2)
        llmobs._inject_llmobs_context(ctx, {})
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) is None
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY) is None


def test_inject_unsafe_agent_name_skips_name_keeps_id(llmobs):
    """An agent name with a comma (illegal in tagset values) is skipped; the id still propagates."""
    with llmobs.agent(name="Researcher, v2") as agent_span:
        ctx = Context(trace_id=1, span_id=2)
        llmobs._inject_llmobs_context(ctx, {})
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) == str(agent_span.span_id)
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY) is None


def test_inject_oversized_agent_name_truncated(llmobs):
    """An agent name that would overflow the tagset budget is truncated rather than dropped."""
    # Build a name large enough to overflow the budget; it must use only safe chars.
    oversized_name = "A" * 500
    with llmobs.agent(name=oversized_name) as agent_span:
        ctx = Context(trace_id=1, span_id=2)
        llmobs._inject_llmobs_context(ctx, {})
    propagated_name = ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY)
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) == str(agent_span.span_id)
    # The name must be truncated (not absent, not the full oversized value).
    assert propagated_name is not None
    assert len(propagated_name) < len(oversized_name)
    # The truncated name must consist only of the safe chars we used.
    assert set(propagated_name) == {"A"}


def test_inject_agent_name_with_equals_propagates(llmobs):
    """`=` is legal in tagset values (only illegal in keys), so a name with `=` must propagate."""
    with llmobs.agent(name="model=gpt4") as agent_span:
        ctx = Context(trace_id=1, span_id=2)
        llmobs._inject_llmobs_context(ctx, {})
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_ID_KEY) == str(agent_span.span_id)
    assert ctx._meta.get(PROPAGATED_PARENT_AGENT_NAME_KEY) == "model=gpt4"


def test_inject_agent_name_with_equals_survives_header_roundtrip(llmobs):
    """A name with `=` encodes into x-datadog-tags and decodes back unchanged (value-until-comma)."""
    with llmobs.agent(name="model=gpt4") as agent_span:
        headers = llmobs.inject_distributed_headers({}, span=agent_span)
    tags_header = headers.get("x-datadog-tags", "")
    assert "_dd.p.llmobs_parent_agent_name=model=gpt4" in tags_header
    assert "_dd.propagation_error" not in tags_header


def test_inject_unsafe_agent_name_does_not_drop_header(llmobs):
    """Critical regression: an unsafe agent name must not poison x-datadog-tags.

    The agent_attribution id key is digit-safe and the name key is skipped when unsafe, so
    the full _dd.p.* tagset still encodes and ml_app / llmobs_trace_id survive on the wire.
    """
    with llmobs.agent(name="Researcher, v2") as agent_span:
        headers = llmobs.inject_distributed_headers({}, span=agent_span)
    tags_header = headers.get("x-datadog-tags", "")
    # Header is present and still carries the pre-existing llmobs keys (not dropped).
    assert "_dd.p.llmobs_ml_app" in tags_header
    assert "_dd.p.llmobs_parent_agent_id={}".format(agent_span.span_id) in tags_header
    # The unsafe name was skipped, so no propagation error and no name key.
    assert "_dd.p.llmobs_parent_agent_name" not in tags_header
    assert "_dd.propagation_error" not in tags_header


def test_distributed_agent_attribution_round_trip(llmobs, llmobs_events):
    """Inbound _dd.p.* agent keys are seeded onto the local context and surfaced on a child."""
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_PARENT_AGENT_ID_KEY] = "987654321"
    ctx._meta[PROPAGATED_PARENT_AGENT_NAME_KEY] = "upstream_agent"
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.tool(name="downstream_tool"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["meta"]["agent_attribution"] == {
        "parent_agent_name": "upstream_agent",
        "parent_agent_span_id": "987654321",
    }


def test_distributed_agent_attribution_round_trip_no_name(llmobs, llmobs_events):
    """Old-SDK upstream propagates only the id; the name resolves to None (backend fills it)."""
    ctx = _make_upstream_llmobs_context(_DECIMAL_TRACE_ID)
    ctx._meta[PROPAGATED_PARENT_AGENT_ID_KEY] = "987654321"
    llmobs._instance._activate_llmobs_distributed_context({}, ctx)
    with llmobs.tool(name="downstream_tool"):
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0]["meta"]["agent_attribution"] == {
        "parent_agent_name": None,
        "parent_agent_span_id": "987654321",
    }


def test_agent_attribution_propagates_across_asyncio_task(llmobs, llmobs_events, patched_asyncio):
    """A tool span created in an asyncio task under an agent still attributes to that agent.

    The in-process llmobs context handed to the child task travels through
    ``_current_trace_context()``; it must carry the agent id/name so the child resolves
    attribution instead of silently dropping it.
    """
    import asyncio

    holder = {}

    async def main():
        with llmobs.agent(name="my_agent") as agent_span:
            holder["span_id"] = str(agent_span.span_id)

            async def child():
                with llmobs.tool(name="async_tool"):
                    pass

            await asyncio.create_task(child())

    asyncio.run(main())
    matches = [e for e in llmobs_events if e["name"] == "async_tool"]
    assert len(matches) == 1, f"expected exactly one async_tool event, got {len(matches)}"
    assert matches[0]["meta"].get("agent_attribution") == {
        "parent_agent_name": "my_agent",
        "parent_agent_span_id": holder["span_id"],
    }
