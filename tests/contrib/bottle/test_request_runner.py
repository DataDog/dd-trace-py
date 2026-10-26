"""Shared callback execution and Bottle adapters preserve application behavior."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ddtrace.contrib._events.bottle import BottleBlockResponseEvent
from ddtrace.contrib._events.bottle import BottleRequestStartEvent
from ddtrace.contrib._events.bottle import BottleResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.bottle.trace import _run_request
from ddtrace.contrib.internal.web_framework import complete_request_safely
from ddtrace.contrib.internal.web_framework import run_web_framework_request
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.utils import assert_span_http_status_code


class Cancelled(BaseException):
    pass


@pytest.fixture(params=[run_web_framework_request, _run_request], ids=["shared", "bottle"])
def runner(request):
    return request.param


@pytest.fixture
def event():
    return WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_method="GET",
        request_url="http://localhost/hello/world",
        request_headers={"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"},
        request_route="/hello/<name>",
        resource="GET /hello/<name>",
        headers_case_sensitive=True,
        activate_distributed_headers=True,
        distributed_headers_config_override=True,
    )


def test_metadata_hooks_bracket_callback_and_precede_span_completion(runner, event, tracer, test_spans):
    order = []
    result = object()
    captured = []

    def prepare():
        assert tracer.current_span() is None
        order.append("prepare")
        return event

    def callback(name, *, greeting):
        assert (name, greeting) == ("world", "hello")
        span = tracer.current_span()
        assert span is not None
        assert not span.finished
        captured.append(span)
        order.append("callback")
        return result

    def complete(request_event, returned, error):
        assert request_event is event
        assert returned is result
        assert error is None
        assert tracer.current_span() is captured[0]
        assert not captured[0].finished
        order.append("complete")
        request_event.response_status_code = 201
        request_event.request_route = "/resolved/<name>"
        request_event.resource = "GET /resolved/<name>"

    returned = runner(callback, ("world",), {"greeting": "hello"}, prepare=prepare, complete=complete)
    assert returned is result
    assert order == ["prepare", "callback", "complete"]
    assert test_spans.pop() == captured
    (span,) = captured
    assert span.finished
    assert span.resource == "GET /resolved/<name>"
    assert span.get_tag("http.route") == "/resolved/<name>"
    assert span.trace_id == 123
    assert span.parent_id == 456
    assert_span_http_status_code(span, 201)
    assert tracer.current_span() is None


@pytest.mark.parametrize("failure", [None, ValueError("callback failed")])
def test_disabled_tracing_runs_metadata_hooks(runner, event, tracer, test_spans, monkeypatch, failure):
    monkeypatch.setattr(tracer, "enabled", False)
    result = object()
    callback = Mock(return_value=result, side_effect=failure)
    prepare = Mock(return_value=event)
    complete = Mock()

    if failure is None:
        assert runner(callback, (1,), {"key": 2}, prepare=prepare, complete=complete) is result
    else:
        with pytest.raises(ValueError) as caught:
            runner(callback, (1,), {"key": 2}, prepare=prepare, complete=complete)
        assert caught.value is failure

    callback.assert_called_once_with(1, key=2)
    prepare.assert_called_once_with()
    complete.assert_called_once_with(event, result if failure is None else None, failure)
    assert test_spans.pop() == []
    assert tracer.current_span() is None


@pytest.mark.parametrize("failure", [ValueError("callback failed"), Cancelled("cancelled")])
def test_completion_observes_original_exception_and_restores_parent(runner, event, tracer, test_spans, failure):
    event.activate_distributed_headers = False
    callback = Mock(side_effect=failure)
    complete = Mock()
    with tracer.trace("parent") as parent:
        with pytest.raises(type(failure)) as caught:
            runner(callback, (), {}, prepare=lambda: event, complete=complete)
        assert caught.value is failure
        callback.assert_called_once_with()
        complete.assert_called_once_with(event, None, failure)
        assert tracer.current_span() is parent
        assert not parent.finished
    spans = test_spans.pop()
    request_span = next(span for span in spans if span is not parent)
    assert len(spans) == 2
    assert request_span.finished
    assert request_span.error == 1
    assert request_span.get_tag("error.message") == str(failure)
    assert tracer.current_span() is None


@pytest.mark.parametrize("stage", ["prepare", "complete"])
@pytest.mark.parametrize("failure_type", [None, ValueError, Cancelled])
def test_metadata_failure_preserves_result_or_exception(runner, event, tracer, test_spans, stage, failure_type):
    failure = failure_type("callback failed") if failure_type is not None else None
    result = object()
    callback = Mock(return_value=result, side_effect=failure)
    prepare = Mock(return_value=event)
    complete = Mock()
    (prepare if stage == "prepare" else complete).side_effect = RuntimeError("metadata failed")

    if failure is None:
        assert runner(callback, (1,), {"key": 2}, prepare=prepare, complete=complete) is result
    else:
        with pytest.raises(type(failure)) as caught:
            runner(callback, (1,), {"key": 2}, prepare=prepare, complete=complete)
        assert caught.value is failure
        assert caught.value.__context__ is None

    callback.assert_called_once_with(1, key=2)
    prepare.assert_called_once_with()
    spans = test_spans.pop()
    if stage == "prepare":
        complete.assert_not_called()
        assert spans == []
    else:
        complete.assert_called_once_with(event, result if failure is None else None, failure)
        (span,) = spans
        assert span.finished
        assert span.error == int(failure is not None)
        assert span.get_tag("error.message") == (str(failure) if failure is not None else None)
    assert tracer.current_span() is None


def test_returned_iterator_is_not_consumed_or_wrapped(runner, event, tracer, test_spans):
    iterated = []

    def body():
        iterated.append(tracer.current_span())
        yield b"body"

    result = body()
    complete = Mock()
    returned = runner(lambda: result, (), {}, prepare=lambda: event, complete=complete)
    assert returned is result
    assert iterated == []
    complete.assert_called_once_with(event, result, None)
    (span,) = test_spans.pop()
    assert span.finished
    assert list(returned) == [b"body"]
    assert iterated == [None]


@pytest.mark.parametrize("failure", [None, ValueError("callback failed"), Cancelled("cancelled")])
def test_bottle_context_lifecycle_preserves_exception(event, tracer, failure):
    started = Mock()
    ended = Mock()
    generic_started = Mock()
    parent = core.current
    core.on("context.started.bottle.request", started)
    core.on("context.ended.bottle.request", ended)
    core.on("context.started.web.request", generic_started)
    try:
        callback = Mock(return_value="result", side_effect=failure)
        if failure is None:
            assert _run_request(callback, (), {}, prepare=lambda: event, complete=Mock()) == "result"
        else:
            with pytest.raises(type(failure)) as caught:
                _run_request(callback, (), {}, prepare=lambda: event, complete=Mock())
            assert caught.value is failure
    finally:
        core.reset_listeners("context.started.bottle.request", started)
        core.reset_listeners("context.ended.bottle.request", ended)
        core.reset_listeners("context.started.web.request", generic_started)

    started.assert_called_once()
    ended.assert_called_once()
    generic_started.assert_not_called()
    ctx, exc_info = ended.call_args.args
    assert ctx is started.call_args.args[0]
    assert ctx.identifier == "bottle.request"
    assert exc_info[1] is failure
    assert exc_info[0] is (type(failure) if failure is not None else None)
    assert core.current is parent
    assert tracer.current_span() is None


@pytest.mark.parametrize("block_at", [None, "start", "response"])
def test_typed_notifications_reach_subscribers(event, tracer, test_spans, block_at):
    notifications = []
    block = SimpleNamespace(status_code=429, type="none", location="")

    class Observer(Subscriber):
        event_names = (
            BottleRequestStartEvent.event_name,
            BottleResponseEvent.event_name,
            BottleBlockResponseEvent.event_name,
        )

        @classmethod
        def on_event(cls, notification):
            notifications.append(notification)
            if isinstance(notification, BottleBlockResponseEvent):
                assert notification.block is block
                notification.response = (429, {"Content-Type": "text/plain"}, b"blocked")
            else:
                assert notification.request_event is event
                if isinstance(notification, BottleRequestStartEvent):
                    assert notification.path_params == {"name": "world"}
                    if block_at == "start":
                        raise BlockingException(block)
                else:
                    assert event.response_status_code == 200
                    if block_at == "response":
                        raise BlockingException(block)

    callback = Mock(return_value="allowed")

    def complete(request_event, result, error):
        request_event.response_status_code = getattr(result, "status_code", 200)

    try:
        result = _run_request(callback, (), {"name": "world"}, prepare=lambda: event, complete=complete)
    finally:
        Observer.unregister()

    assert [type(notification) for notification in notifications] == [
        BottleRequestStartEvent,
        BottleResponseEvent,
    ] + ([BottleBlockResponseEvent] if block_at else [])
    if block_at:
        assert result.status_code == 429
        assert result.body == b"blocked"
        assert result.headers["Content-Type"] == "text/plain"
    else:
        assert result == "allowed"
    assert callback.call_count == int(block_at != "start")
    (span,) = test_spans.pop()
    assert span.finished
    assert span.error == 0
    assert_span_http_status_code(span, 429 if block_at else 200)
    assert tracer.current_span() is None


@pytest.mark.parametrize("replace", [False, True])
def test_shared_execution_adapter_observes_completed_metadata(event, tracer, test_spans, replace):
    event.activate_distributed_headers = False
    failure = Cancelled("callback interrupted")
    order = []
    callback_result = object()
    replacement = object()

    def callback():
        order.append("callback")
        if replace:
            raise failure
        return callback_result

    def complete(request_event, result, error):
        assert request_event is event
        assert tracer.current_span() is not None
        order.append((result, error))
        request_event.response_status_code = 500 if error else 202

    def execute(request_event, invoke):
        assert core.current.event is request_event
        order.append("before")
        try:
            result = invoke()
        except Cancelled as exc:
            assert exc is failure
            assert event.response_status_code == 500
            complete_request_safely(complete, event, replacement, None)
            return replacement
        assert event.response_status_code == 202
        order.append("after")
        return result

    result = run_web_framework_request(callback, (), {}, prepare=lambda: event, complete=complete, execute=execute)
    if replace:
        assert result is replacement
        assert order == ["before", "callback", (None, failure), (replacement, None)]
    else:
        assert result is callback_result
        assert order == ["before", "callback", (callback_result, None), "after"]
    (span,) = test_spans.pop()
    assert span.finished
    assert span.error == 0
    assert_span_http_status_code(span, 202)
    assert tracer.current_span() is None


def test_preparation_failure_bypasses_execution_adapter(tracer):
    callback = Mock(return_value="result")
    execute = Mock()
    complete = Mock()
    assert (
        run_web_framework_request(
            callback,
            (1,),
            {"key": 2},
            prepare=Mock(side_effect=ValueError("metadata failed")),
            complete=complete,
            execute=execute,
        )
        == "result"
    )
    callback.assert_called_once_with(1, key=2)
    execute.assert_not_called()
    complete.assert_not_called()
    assert tracer.current_span() is None
