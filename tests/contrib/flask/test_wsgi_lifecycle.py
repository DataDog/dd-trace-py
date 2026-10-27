"""Response lifetime ownership independent of Flask's tracing and security hooks."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import Mock

import flask
import pytest
from werkzeug.test import EnvironBuilder

from ddtrace.contrib.internal.wsgi.lifecycle import WSGIResponseIterable
from ddtrace.internal import core

from . import BaseFlaskTestCase


@pytest.mark.parametrize("termination", ["exhaust", "close", "error"])
def test_wsgi_scope_isolated_until_response_completion(termination: str) -> None:
    active: ContextVar[str] = ContextVar("request", default="caller")
    finished = []
    closed = []

    @contextmanager
    def scope() -> Iterator[None]:
        token = active.set("request")
        try:
            yield
        finally:
            finished.append(active.get())
            active.reset(token)

    class Response:
        def __iter__(self) -> Iterator[bytes]:
            assert active.get() == "request"
            yield b"first"
            if termination == "error":
                raise ValueError("stream")
            yield b"second"

        def close(self) -> None:
            closed.append(active.get())

    def application() -> Response:
        assert active.get() == "request"
        return Response()

    response = WSGIResponseIterable(scope()).start(application)
    assert active.get() == "caller"
    assert finished == closed == []
    assert next(response) == b"first"
    assert active.get() == "caller"
    if termination == "error":
        with pytest.raises(ValueError, match="stream"):
            next(response)
    elif termination == "exhaust":
        assert list(response) == [b"second"]
    response.close()
    response.close()
    assert finished == closed == ["request"]
    assert list(response) == []
    assert active.get() == "caller"


def test_wsgi_scope_can_close_without_consuming_response() -> None:
    response = Mock()
    scope = MagicMock()
    wrapped = WSGIResponseIterable(scope).start(lambda: response)
    wrapped.close()
    wrapped.close()
    response.close.assert_called_once_with()
    scope.__enter__.assert_called_once_with()
    scope.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize("failure_at", ["application", "iteration", "close"])
def test_wsgi_scope_sees_original_failure(failure_at: str) -> None:
    failure = ValueError("failure")
    scope = MagicMock()
    scope.__exit__.return_value = False
    body = MagicMock()
    body.__iter__.return_value = iter([b"ok"])
    if failure_at == "iteration":
        body.__iter__.side_effect = failure
    if failure_at == "close":
        body.close.side_effect = failure
    application = Mock(return_value=body)
    if failure_at == "application":
        application.side_effect = failure
    with pytest.raises(ValueError) as caught:
        response = WSGIResponseIterable(scope).start(application)
        if failure_at == "iteration":
            next(response)
        else:
            response.close()
    assert caught.value is failure
    assert scope.__exit__.call_count == 1
    assert scope.__exit__.call_args.args[1] is failure
    assert body.close.call_count == int(failure_at != "application")


def test_wsgi_close_failure_preserves_iteration_error() -> None:
    original = ValueError("iteration")
    body = MagicMock()
    body.__iter__.side_effect = original
    body.close.side_effect = RuntimeError("cleanup")
    scope = MagicMock()
    response = WSGIResponseIterable(scope).start(lambda: body)
    with pytest.raises(ValueError) as caught:
        next(response)
    assert caught.value is original
    response.close()
    assert scope.__exit__.call_count == 1
    assert scope.__exit__.call_args.args[1] is original
    body.close.assert_called_once_with()


def test_interleaved_wsgi_responses_restore_caller_context() -> None:
    active: ContextVar[str] = ContextVar("request", default="caller")

    @contextmanager
    def scope(name: str) -> Iterator[None]:
        token = active.set(name)
        try:
            yield
        finally:
            assert active.get() == name
            active.reset(token)

    def body() -> Iterator[bytes]:
        yield active.get().encode()
        yield active.get().encode()

    first = WSGIResponseIterable(scope("first")).start(body)
    second = WSGIResponseIterable(scope("second")).start(body)
    assert next(first) == b"first"
    assert next(second) == b"second"
    assert next(first) == b"first"
    assert active.get() == "caller"
    first.close()
    assert next(second) == b"second"
    second.close()
    assert active.get() == "caller"


# The tests below exercise the installed Flask patch, including the legacy events
# that AppSec and IAST still consume.


class FlaskLifecycleTestCase(BaseFlaskTestCase):
    def call_wsgi(self, path: str = "/") -> Any:
        builder = EnvironBuilder(path=path)
        try:
            return self.app.wsgi_app(builder.get_environ(), lambda status, headers, exc_info=None: None)
        finally:
            builder.close()

    def test_interleaved_streams_preserve_context_and_parentage(self) -> None:
        roots = []
        ended = []
        closed = []

        @self.app.route("/<name>")
        def view(name: str) -> flask.Response:
            roots.append(self.tracer.current_root_span())

            @flask.stream_with_context
            def body() -> Iterator[bytes]:
                for _ in range(2):
                    assert flask.request.view_args["name"] == name
                    with self.tracer.trace("chunk") as span:
                        yield name.encode()
                        assert span.parent_id is not None

            response = self.app.response_class(body())
            response.call_on_close(lambda: closed.append(name))
            return response

        def on_ended(ctx: Any, exc_info: Any) -> None:
            ended.append(ctx.get_item("flask_request_event"))

        core.on("context.ended.wsgi.__call__", on_ended)
        try:
            caller_context = core.current
            first = self.call_wsgi("/first")
            second = self.call_wsgi("/second")
            assert core.current is caller_context
            assert self.tracer.current_span() is None
            assert not flask.has_request_context()
            assert len(ended) == 2
            assert all(event.response_status_code == 200 for event in ended)
            assert all(event.request_route == "/<name>" for event in ended)
            assert all(not root.finished for root in roots)
            assert next(first) == b"first"
            assert next(second) == b"second"
            assert next(first) == b"first"
            assert core.current is caller_context
            assert self.tracer.current_span() is None
            first.close()
            first.close()
            assert roots[0].finished and not roots[1].finished
            assert list(second) == [b"second"]
            second.close()
            assert closed == ["first", "second"]
            assert len(ended) == 2
            assert core.current is caller_context
            assert self.tracer.current_span() is None
            assert not flask.has_request_context()
            spans = self.get_spans()
            requests = [span for span in spans if span.name == "flask.request"]
            responses = [span for span in spans if span.name == "flask.response"]
            assert len(requests) == len(responses) == 2
            for response_span in responses:
                assert response_span.parent_id in [root.span_id for root in requests]
            for chunk in (span for span in spans if span.name == "chunk"):
                assert chunk.parent_id in [span.span_id for span in responses]
        finally:
            core.reset_listeners("context.ended.wsgi.__call__", on_ended)

    def test_close_failure_finishes_request_and_response_spans(self) -> None:
        closed = []

        @self.app.route("/")
        def view() -> flask.Response:
            response = self.app.response_class(iter([b"ok"]))

            def close() -> None:
                closed.append(True)
                raise ValueError("close failed")

            response.call_on_close(close)
            return response

        response = self.call_wsgi()
        with pytest.raises(ValueError, match="close failed"):
            response.close()
        response.close()
        assert closed == [True]
        assert self.tracer.current_span() is None
        spans = self.get_spans()
        assert len([span for span in spans if span.name == "flask.request"]) == 1
        response_span = next(span for span in spans if span.name == "flask.response")
        assert response_span.finished
        assert response_span.error == 1

    def test_existing_view_security_result_contracts(self) -> None:
        calls = []

        @self.app.route("/<name>")
        def view(name: str) -> str:
            calls.append(name)
            return name

        def replace_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
            return {"name": "tainted"}

        core.on("flask.wrapped_view", replace_kwargs, "check_kwargs")
        try:
            response = self.call_wsgi("/original")
            assert b"".join(response) == b"tainted"
            response.close()
            assert calls == ["tainted"]
        finally:
            core.reset_listeners("flask.wrapped_view", replace_kwargs)

        def block(kwargs: dict[str, Any]) -> Any:
            return lambda: ("blocked", 403)

        core.on("flask.wrapped_view", block, "callbacks")
        try:
            response = self.call_wsgi("/original")
            assert b"".join(response) == b"blocked"
            response.close()
            assert calls == ["tainted"]
            roots = [span for span in self.get_spans() if span.name == "flask.request"]
            assert any(span.get_tag("http.status_code") == "403" for span in roots)
        finally:
            core.reset_listeners("flask.wrapped_view", block)
