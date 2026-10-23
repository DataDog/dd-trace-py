"""The shared request API preserves application behavior around metadata hooks."""

from unittest.mock import Mock

import pytest

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import run_web_framework_request
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from tests.utils import assert_span_http_status_code


class Cancelled(BaseException):
    pass


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


def test_metadata_hooks_bracket_callback_and_precede_span_completion(event, tracer, test_spans):
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

    returned = run_web_framework_request(
        callback, ("world",), {"greeting": "hello"}, prepare=prepare, complete=complete
    )
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


def test_disabled_tracing_skips_metadata_hooks(tracer, test_spans, monkeypatch):
    monkeypatch.setattr(tracer, "enabled", False)
    result = object()
    callback = Mock(return_value=result)
    prepare = Mock()
    complete = Mock()

    assert run_web_framework_request(callback, (1,), {"key": 2}, prepare=prepare, complete=complete) is result
    callback.assert_called_once_with(1, key=2)
    prepare.assert_not_called()
    complete.assert_not_called()
    assert test_spans.pop() == []


@pytest.mark.parametrize("failure", [ValueError("callback failed"), Cancelled("cancelled")])
def test_completion_observes_original_exception_and_restores_parent(event, tracer, test_spans, failure):
    event.activate_distributed_headers = False
    callback = Mock(side_effect=failure)
    complete = Mock()
    with tracer.trace("parent") as parent:
        with pytest.raises(type(failure)) as caught:
            run_web_framework_request(callback, (), {}, prepare=lambda: event, complete=complete)
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
def test_metadata_failure_preserves_result_or_exception(event, tracer, test_spans, stage, failure_type):
    failure = failure_type("callback failed") if failure_type is not None else None
    result = object()
    callback = Mock(return_value=result, side_effect=failure)
    prepare = Mock(return_value=event)
    complete = Mock()
    (prepare if stage == "prepare" else complete).side_effect = RuntimeError("metadata failed")

    if failure is None:
        assert run_web_framework_request(callback, (1,), {"key": 2}, prepare=prepare, complete=complete) is result
    else:
        with pytest.raises(type(failure)) as caught:
            run_web_framework_request(callback, (1,), {"key": 2}, prepare=prepare, complete=complete)
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


def test_returned_iterator_is_not_consumed_or_wrapped(event, tracer, test_spans):
    iterated = []

    def body():
        iterated.append(tracer.current_span())
        yield b"body"

    result = body()
    complete = Mock()
    returned = run_web_framework_request(lambda: result, (), {}, prepare=lambda: event, complete=complete)
    assert returned is result
    assert iterated == []
    complete.assert_called_once_with(event, result, None)
    (span,) = test_spans.pop()
    assert span.finished
    assert list(returned) == [b"body"]
    assert iterated == [None]
