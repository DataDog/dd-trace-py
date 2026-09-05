import json
import os
import signal
import subprocess
import time
from types import SimpleNamespace

from azure.durable_functions.orchestrator import Orchestrator
from azure.functions import OrchestrationContext
import pytest

from ddtrace import config
from ddtrace.constants import SPAN_KIND
import ddtrace.contrib  # noqa: F401
from ddtrace.contrib.internal.azure_durable_functions.patch import patched_get_current_activity_context
from ddtrace.contrib.internal.azure_functions._worker import _run_sync_with_context
from ddtrace.contrib.internal.azure_functions.shared import patched_get_functions
from ddtrace.contrib.internal.azure_functions.shared import wrap_durable_trigger
from ddtrace.contrib.internal.azure_functions.shared import wrap_orchestration_trigger
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import schematize_cloud_faas_operation
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer
from tests.webclient import Client


DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
SNAPSHOT_IGNORES = ["meta.http.url", "meta.test.deployment_verification"]


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    env = os.environ.copy()
    env.update(env_vars)

    port = 7072
    env["AZURE_FUNCTIONS_TEST_PORT"] = str(port)
    env["DD_TRACE_STATS_COMPUTATION_ENABLED"] = "False"  # disable stats computation to avoid potential flakes in tests

    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        ["func", "start", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=os.path.join(os.path.dirname(__file__), "azure_function_app"),
    )
    try:
        client = Client(f"http://0.0.0.0:{port}")
        # Wait for the server to start up
        try:
            client.wait(delay=0.5)
            yield client
            client.get_ignored("/shutdown")
        except Exception:
            pass
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _wait_for_durable_completion(client: Client, response) -> None:
    if response.status_code == 200:
        return

    assert response.status_code == 202
    payload = response.json()
    status_url = payload.get("statusQueryGetUri")
    assert status_url

    for _ in range(20):
        status_response = client.get(status_url, timeout=5)
        if status_response.status_code == 200:
            status_payload = status_response.json()
            if status_payload.get("runtimeStatus") == "Completed":
                return
        time.sleep(0.5)

    pytest.fail("Durable orchestration did not complete before timeout")


@pytest.mark.parametrize(
    "func_name, trigger_name, context_name",
    [
        ("sample_activity", "Activity", "azure.durable_functions.patched_activity"),
        ("sample_entity", "Entity", "azure.durable_functions.patched_entity"),
    ],
)
def test_trigger_wrapper(func_name, trigger_name, context_name):
    with scoped_tracer() as tracer:

        def trigger_func():
            return "ok"

        wrapped = wrap_durable_trigger(
            trigger_func,
            func_name,
            trigger_name,
            context_name,
        )
        assert wrapped() == "ok"

        spans = TracerSpanContainer(tracer).pop()
        assert len(spans) == 1
        span = spans[0]

        expected_name = schematize_cloud_faas_operation(
            "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
        )
        assert span.name == expected_name
        assert span.service == int_service(None, config.azure_functions)
        assert span.resource == f"{trigger_name} {func_name}"
        assert span.span_type == SpanTypes.SERVERLESS
        assert span.get_tag("aas.function.name") == func_name  # codespell:ignore
        assert span.get_tag("aas.function.trigger") == trigger_name  # codespell:ignore
        assert span.get_tag(SPAN_KIND) == SpanKind.INTERNAL


def test_durable_trigger_continues_http_trace_across_invocations():
    with scoped_tracer() as tracer:
        with tracer.trace("http.request") as http_span:
            traceparent, tracestate = patched_get_current_activity_context(lambda: (None, None), None, (), {})

        assert traceparent is not None
        carrier = {"traceparent": traceparent}
        if tracestate is not None:
            carrier["tracestate"] = tracestate

        invocation_context = SimpleNamespace(
            trace_context=SimpleNamespace(trace_parent=carrier["traceparent"], trace_state=carrier.get("tracestate"))
        )

        def invoke_trigger(*_):
            wrapped = wrap_durable_trigger(
                lambda: "ok",
                "sample_activity",
                "Activity",
                "azure.durable_functions.patched_activity",
            )
            return wrapped()

        assert _run_sync_with_context(invoke_trigger, None, ("invocation-id", invocation_context), {}) == "ok"

        spans = TracerSpanContainer(tracer).pop()
        activity_span = next(span for span in spans if span.resource == "Activity sample_activity")
        assert activity_span.trace_id == http_span.trace_id
        assert activity_span.parent_id == http_span.span_id


def test_durable_trigger_preserves_propagated_keep_when_host_clears_sampled_flag():
    carrier = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        "tracestate": "dd=s:2",
    }
    invocation_context = SimpleNamespace(
        trace_context=SimpleNamespace(trace_parent=carrier["traceparent"], trace_state=carrier["tracestate"])
    )
    with scoped_tracer() as tracer:

        def invoke_trigger(*_):
            wrapped = wrap_durable_trigger(
                lambda: "ok",
                "sample_activity",
                "Activity",
                "azure.durable_functions.patched_activity",
            )
            return wrapped()

        assert _run_sync_with_context(invoke_trigger, None, ("invocation-id", invocation_context), {}) == "ok"
        span = TracerSpanContainer(tracer).pop()[0]
        assert span.context.sampling_priority == 2


def _orchestration_context(has_previous_activation: bool = False, parent_carrier=None) -> OrchestrationContext:
    history = [
        {
            "EventType": 12,
            "EventId": -1,
            "IsPlayed": False,
            "Timestamp": "2020-01-01T00:00:00Z",
        },
        {
            "EventType": 0,
            "EventId": 0,
            "IsPlayed": False,
            "Timestamp": "2020-01-01T00:00:00Z",
            "Name": "sample_orchestrator",
            "Input": None,
            "Version": None,
            "ParentTraceContext": (
                {
                    "TraceParent": parent_carrier["traceparent"],
                    "TraceState": parent_carrier.get("tracestate"),
                }
                if parent_carrier is not None
                else None
            ),
        },
    ]
    if has_previous_activation:
        history.extend(
            [
                {
                    "EventType": 13,
                    "EventId": 1,
                    "IsPlayed": False,
                    "Timestamp": "2020-01-01T00:00:01Z",
                },
                {
                    "EventType": 12,
                    "EventId": 2,
                    "IsPlayed": False,
                    "Timestamp": "2020-01-01T00:00:02Z",
                },
            ]
        )

    body = {
        "history": history,
        "instanceId": "abc-123",
        "isReplaying": has_previous_activation,
        "parentInstanceId": None,
    }
    return OrchestrationContext(json.dumps(body))


def test_orchestration_trigger_wrapper():
    def orchestrator(_):
        return "ok"

    handler = Orchestrator.create(orchestrator)
    wrapped = wrap_orchestration_trigger(handler, "sample_orchestrator", "context")

    with scoped_tracer() as tracer:
        result = wrapped(_orchestration_context())

        assert json.loads(result)["output"] == "ok"
        spans = TracerSpanContainer(tracer).pop()
        assert len(spans) == 1
        span = spans[0]
        expected_name = schematize_cloud_faas_operation(
            "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
        )
        assert span.name == expected_name
        assert span.service == int_service(None, config.azure_functions)
        assert span.resource == "Orchestration sample_orchestrator"
        assert span.span_type == SpanTypes.SERVERLESS
        assert span.get_tag("aas.function.name") == "sample_orchestrator"  # codespell:ignore
        assert span.get_tag("aas.function.trigger") == "Orchestration"  # codespell:ignore
        assert span.get_tag(SPAN_KIND) == SpanKind.SERVER


def test_orchestration_trigger_uses_parent_from_execution_started_history():
    def orchestrator(_):
        return "ok"

    handler = Orchestrator.create(orchestrator)
    wrapped = wrap_orchestration_trigger(handler, "sample_orchestrator", "context")

    with scoped_tracer() as tracer:
        with tracer.trace("http.request") as http_span:
            traceparent, tracestate = patched_get_current_activity_context(lambda: (None, None), None, (), {})

        assert traceparent is not None
        carrier = {"traceparent": traceparent}
        if tracestate is not None:
            carrier["tracestate"] = tracestate

        wrapped(_orchestration_context(parent_carrier=carrier))
        orchestration_span = next(
            span for span in TracerSpanContainer(tracer).pop() if span.resource == "Orchestration sample_orchestrator"
        )
        assert orchestration_span.trace_id == http_span.trace_id
        assert orchestration_span.parent_id == http_span.span_id


def test_orchestration_trigger_is_wrapped_during_function_discovery():
    def orchestrator(_):
        return "ok"

    handler = Orchestrator.create(orchestrator)

    class Trigger:
        name = "context"

        def get_binding_name(self):
            return "orchestrationTrigger"

        def get_dict_repr(self):
            return {}

    class Function:
        _func = handler

        def get_trigger(self):
            return Trigger()

        def get_function_name(self):
            return "sample_orchestrator"

        def get_user_function(self):
            return self._func

    function = Function()
    patched_get_functions(lambda: [function], None, (), {})

    assert function._func is not handler
    assert function._func.__wrapped__.__code__ is handler.__code__


def test_activity_trigger_wrapper_traces_error():
    def activity():
        raise RuntimeError("activity failed")

    wrapped = wrap_durable_trigger(
        activity,
        "sample_activity",
        "Activity",
        "azure.durable_functions.patched_activity",
    )

    with scoped_tracer() as tracer:
        with pytest.raises(RuntimeError, match="activity failed"):
            wrapped()

        span = TracerSpanContainer(tracer).pop()[0]
        assert span.resource == "Activity sample_activity"
        assert span.error == 1


def test_orchestration_trigger_wrapper_skips_previous_activation():
    def orchestrator(_):
        return "ok"

    handler = Orchestrator.create(orchestrator)
    wrapped = wrap_orchestration_trigger(handler, "sample_orchestrator", "context")

    with scoped_tracer() as tracer:
        result = wrapped(context=_orchestration_context(has_previous_activation=True))

        assert json.loads(result)["output"] == "ok"
        assert TracerSpanContainer(tracer).pop() == []


def test_orchestration_trigger_wrapper_traces_error_after_previous_activation():
    calls = 0

    def orchestrator(_):
        nonlocal calls
        calls += 1
        if False:
            yield None
        raise RuntimeError("orchestration failed")

    handler = Orchestrator.create(orchestrator)
    wrapped = wrap_orchestration_trigger(handler, "sample_orchestrator", "context")

    with scoped_tracer() as tracer:
        with tracer.trace("http.request") as http_span:
            traceparent, tracestate = patched_get_current_activity_context(lambda: (None, None), None, (), {})
        assert traceparent is not None
        parent_carrier = {"traceparent": traceparent}
        if tracestate is not None:
            parent_carrier["tracestate"] = tracestate

        with pytest.raises(Exception, match="orchestration failed"):
            wrapped(context=_orchestration_context(has_previous_activation=True, parent_carrier=parent_carrier))

        assert calls == 1
        spans = TracerSpanContainer(tracer).pop()
        orchestration_spans = [span for span in spans if span.resource == "Orchestration sample_orchestrator"]
        assert len(orchestration_spans) == 1
        span = orchestration_spans[0]
        assert span.resource == "Orchestration sample_orchestrator"
        assert span.get_tag(SPAN_KIND) == SpanKind.SERVER
        assert span.error == 1
        assert span.trace_id == http_span.trace_id
        assert span.parent_id == http_span.span_id


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_activity_trigger_end_to_end(azure_functions_client: Client) -> None:
    response = azure_functions_client.get("/api/startactivity", headers=DEFAULT_HEADERS)
    _wait_for_durable_completion(azure_functions_client, response)
    assert response.status_code in (200, 202)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_entity_trigger_end_to_end(azure_functions_client: Client) -> None:
    response = azure_functions_client.get("/api/startentity", headers=DEFAULT_HEADERS)
    _wait_for_durable_completion(azure_functions_client, response)
    assert response.status_code in (200, 202)
