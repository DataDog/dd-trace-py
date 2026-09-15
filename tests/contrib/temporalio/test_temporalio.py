from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest
import temporalio.activity
from temporalio.client import Client
from temporalio.client import Interceptor as ClientInterceptor
from temporalio.client import OutboundInterceptor as ClientOutboundInterceptor
from temporalio.converter import PayloadConverter
from temporalio.worker import ActivityInboundInterceptor

from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.temporalio.patch import unpatch
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from tests.utils import override_config
from tests.utils import override_global_tracer


# AIDEV-NOTE: Workflow code is replayed by Temporal. Add workflow-run acceptance tests only after
# replay characterization proves that spans and propagation remain deterministic.


class _RecordingClientOutbound(ClientOutboundInterceptor):
    def __init__(self, next_interceptor: ClientOutboundInterceptor) -> None:
        super().__init__(next_interceptor)
        self.inputs: dict[str, Any] = {}

    async def start_workflow(self, input_data: Any) -> str:
        self.inputs["start_workflow"] = input_data
        return "workflow-started"

    async def signal_workflow(self, input_data: Any) -> str:
        self.inputs["signal_workflow"] = input_data
        return "workflow-signaled"

    async def query_workflow(self, input_data: Any) -> str:
        self.inputs["query_workflow"] = input_data
        return "workflow-queried"


class _RecordingClientInterceptor(ClientInterceptor):
    def __init__(self) -> None:
        self.outbound: _RecordingClientOutbound | None = None

    def intercept_client(self, next_interceptor: ClientOutboundInterceptor) -> ClientOutboundInterceptor:
        self.outbound = _RecordingClientOutbound(next_interceptor)
        return self.outbound


class _ExecutingActivityInbound(ActivityInboundInterceptor):
    def __init__(self) -> None:
        pass

    async def execute_activity(self, input_data: Any) -> Any:
        result = input_data.fn(*input_data.args)
        if inspect.isawaitable(result):
            return await result
        return result


def _new_client(recorder: _RecordingClientInterceptor) -> Client:
    return Client(cast(Any, object()), interceptors=[recorder])


def _datadog_interceptor(client: Client) -> Any:
    interceptors = client.config()["interceptors"]
    matches = [
        interceptor
        for interceptor in interceptors
        if type(interceptor).__module__.startswith("ddtrace.contrib.internal.temporalio")
    ]
    assert len(matches) == 1, "Client construction must install exactly one Datadog Temporal interceptor"
    return matches[0]


def _input_for(operation: str) -> SimpleNamespace:
    values: dict[str, Any] = {
        "args": [],
        "headers": {},
        "id": "workflow-id-secret",
        "rpc_metadata": {},
        "rpc_timeout": None,
        "run_id": "run-id-secret",
    }
    if operation == "start_workflow":
        values.update(workflow="GreetingWorkflow", start_signal=None)
    elif operation == "signal_workflow":
        values.update(signal="GreetingSignal")
    else:
        values.update(query="GreetingQuery")
    return SimpleNamespace(**values)


def _operation_span(spans: list[Any], name: str) -> Any:
    matches = [span for span in spans if span.name == name]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "span_name", "kind", "resource", "result"),
    [
        ("start_workflow", "temporal.start_workflow", SpanKind.PRODUCER, "GreetingWorkflow", "workflow-started"),
        ("signal_workflow", "temporal.signal_workflow", SpanKind.PRODUCER, "GreetingSignal", "workflow-signaled"),
        ("query_workflow", "temporal.query_workflow", SpanKind.CLIENT, "GreetingQuery", "workflow-queried"),
    ],
)
async def test_client_operations(
    tracer: Any,
    test_spans: Any,
    operation: str,
    span_name: str,
    kind: str,
    resource: str,
    result: str,
) -> None:
    recorder = _RecordingClientInterceptor()
    input_data = _input_for(operation)

    with override_global_tracer(tracer), override_config("temporalio", {"service": "temporal-test"}):
        client = _new_client(recorder)
        assert await getattr(client._impl, operation)(input_data) == result

    _datadog_interceptor(client)
    span = _operation_span(test_spans.pop(), span_name)
    assert span.service == "temporal-test"
    assert span.span_type == SpanTypes.WORKER
    assert span.resource == resource
    assert span.get_tag(SPAN_KIND) == kind
    assert span.get_tag("component") == "temporalio"
    assert span.get_tag("messaging.system") == "temporal"
    assert "workflow-id-secret" not in repr(span.get_tags())
    assert "run-id-secret" not in repr(span.get_tags())


def _activity_info() -> SimpleNamespace:
    return SimpleNamespace(
        activity_id="activity-id-secret",
        activity_type="GreetingActivity",
        attempt=1,
        namespace="default",
        task_queue="greetings",
        workflow_id="workflow-id-secret",
        workflow_run_id="run-id-secret",
        workflow_type="GreetingWorkflow",
    )


def _decoded_headers(headers: dict[str, Any]) -> str:
    return repr(PayloadConverter.default.from_payloads(list(headers.values())))


def _sync_activity(name: str) -> str:
    return f"hello {name}"


async def _async_activity(name: str) -> str:
    return f"hello {name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_fn", [_sync_activity, _async_activity])
async def test_activity_execution_and_distributed_context(
    monkeypatch: pytest.MonkeyPatch,
    tracer: Any,
    test_spans: Any,
    activity_fn: Any,
) -> None:
    recorder = _RecordingClientInterceptor()
    start_input = _input_for("start_workflow")
    existing_payload = PayloadConverter.default.to_payloads(["keep-me"])[0]
    start_input.headers["application-header"] = existing_payload

    with override_global_tracer(tracer):
        client = _new_client(recorder)
        with tracer.trace("test.parent") as parent:
            assert await client._impl.start_workflow(start_input) == "workflow-started"

        injected_headers = start_input.headers
        assert injected_headers["application-header"] is existing_payload
        assert len(injected_headers) > 1
        assert str(parent._trace_id_64bits) in _decoded_headers(injected_headers)

        monkeypatch.setattr(temporalio.activity, "info", _activity_info)
        activity_interceptor = _datadog_interceptor(client).intercept_activity(_ExecutingActivityInbound())
        activity_input = SimpleNamespace(fn=activity_fn, args=["Temporal"], executor=None, headers=injected_headers)
        assert await activity_interceptor.execute_activity(activity_input) == "hello Temporal"

    spans = test_spans.pop()
    producer = _operation_span(spans, "temporal.start_workflow")
    consumer = _operation_span(spans, "temporal.run_activity")
    assert consumer.trace_id == producer.trace_id
    assert consumer.parent_id == producer.span_id
    assert consumer.span_type == SpanTypes.WORKER
    assert consumer.resource == "GreetingActivity"
    assert consumer.get_tag(SPAN_KIND) == SpanKind.CONSUMER
    assert consumer.get_tag("component") == "temporalio"
    assert consumer.get_tag("messaging.system") == "temporal"


@pytest.mark.asyncio
async def test_activity_error_preserves_exception(
    monkeypatch: pytest.MonkeyPatch,
    tracer: Any,
    test_spans: Any,
) -> None:
    expected_error = RuntimeError("activity failed")

    def failing_activity() -> None:
        raise expected_error

    with override_global_tracer(tracer):
        client = _new_client(_RecordingClientInterceptor())
        monkeypatch.setattr(temporalio.activity, "info", _activity_info)
        activity_interceptor = _datadog_interceptor(client).intercept_activity(_ExecutingActivityInbound())
        activity_input = SimpleNamespace(fn=failing_activity, args=[], executor=None, headers={})
        with pytest.raises(RuntimeError) as caught:
            await activity_interceptor.execute_activity(activity_input)

    assert caught.value is expected_error
    span = _operation_span(test_spans.pop(), "temporal.run_activity")
    assert span.error == 1
    assert span.get_tag("error.type").endswith("RuntimeError")
    assert span.get_tag("error.message") == "activity failed"


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_trace_or_propagate(tracer: Any, test_spans: Any) -> None:
    recorder = _RecordingClientInterceptor()
    input_data = _input_for("start_workflow")

    with override_global_tracer(tracer), override_config("temporalio", {"enabled": False}):
        client = _new_client(recorder)
        assert await client._impl.start_workflow(input_data) == "workflow-started"

    assert input_data.headers == {}
    assert test_spans.pop() == []


@pytest.mark.asyncio
async def test_unpatch_does_not_trace_or_change_user_interceptors(tracer: Any, test_spans: Any) -> None:
    unpatch()
    recorder = _RecordingClientInterceptor()
    input_data = _input_for("start_workflow")

    with override_global_tracer(tracer):
        client = _new_client(recorder)
        assert await client._impl.start_workflow(input_data) == "workflow-started"

    assert client.config()["interceptors"] == [recorder]
    assert input_data.headers == {}
    assert test_spans.pop() == []
