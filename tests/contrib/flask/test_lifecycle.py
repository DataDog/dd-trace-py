from typing import Any
from typing import Iterator

import flask
import pytest
from werkzeug.test import EnvironBuilder

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.flask.patch import _FlaskWSGIMiddleware
from ddtrace.contrib.internal.flask.patch import patch
from ddtrace.contrib.internal.flask.patch import unpatch
from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.internal import core
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.constants import _SERVICE_SOURCE
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import Span
from ddtrace.trace import Tracer
from ddtrace.trace import tracer as global_tracer
from tests.utils import override_config
from tests.utils import override_global_config

from . import BaseFlaskTestCase


class FlaskLifecycleTestCase(BaseFlaskTestCase):
    def setUp(self):
        super().setUp()
        self.phases = []
        self.roots = []
        self.scopes = []

        def started(ctx):
            if ctx.event.component != "flask":
                return
            self.phases.append("started")
            self.roots.append(ctx.get_item("req_span"))
            self.scopes.append(ctx)
            self.assertFalse(self.roots[-1].finished)
            ctx.event.additional_tags["lifecycle.ended"] = "observed"

        def ended(ctx, *args):
            if ctx.event.component == "flask":
                self.phases.append("ended")

        for name, handler in (("context.started.web.request", started), ("context.ended.web.request", ended)):
            core.on(name, handler)
            self.addCleanup(core.reset_listeners, name, handler)

    def open_response(self, path="/", app=None):
        builder = EnvironBuilder(path=path)
        self.addCleanup(builder.close)
        response = (app or self.app)(builder.get_environ(), lambda status, headers, exc_info=None: lambda data: None)
        self.addCleanup(response.close)
        return response

    def test_stream_completion_and_context_restoration(self):
        chunks = []
        children = []

        @self.app.route("/stream")
        def stream():
            @flask.stream_with_context
            def generate():
                self.assertEqual(flask.request.path, "/stream")
                with self.tracer.trace("stream.child") as child:
                    children.append(child)
                    chunks.append(1)
                    try:
                        yield b"first"
                        self.assertIs(self.tracer.current_span(), child)
                    finally:
                        self.assertIs(self.tracer.current_span(), child)
                yield b"second"

            return flask.Response(generate())

        with self.tracer.trace("caller") as caller:
            response = self.open_response("/stream")
            self.assertEqual(chunks, [])
            self.assertEqual(self.phases, ["started"])
            self.assertIs(self.tracer.current_root_span(), caller)
            self.assertEqual(next(response), b"first")
            response_span = self.scopes[0].get_item("response_span")
            self.assertEqual(children[0].parent_id, response_span.span_id)
            self.assertIs(self.tracer.current_root_span(), caller)
            self.assertEqual(list(response), [b"second"])
            response.close()
            self.assertIs(self.tracer.current_span(), caller)
        self.assertEqual(self.phases, ["started", "ended"])
        self.assertTrue(self.roots[0].finished)
        self.assertTrue(response_span.finished)
        self.assertEqual(self.roots[0].get_tag("lifecycle.ended"), "observed")

    def test_close_before_first_chunk(self):
        closed = []

        @self.app.route("/")
        def index():
            response = flask.Response(iter([b"first", b"second"]))
            response.call_on_close(lambda: closed.append(True))
            return response

        response = self.open_response()
        response.close()
        response.close()
        self.assertEqual(closed, [True])
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_stream_error_and_explicit_close_error_complete_once(self):
        stream_error = ValueError("stream failure")

        def close():
            raise RuntimeError("close failure")

        @self.app.route("/")
        def index():
            def generate():
                yield b"first"
                raise stream_error

            response = flask.Response(generate())
            response.call_on_close(close)
            return response

        response = self.open_response()
        self.assertEqual(next(response), b"first")
        with self.assertRaises(ValueError) as raised:
            next(response)
        self.assertIs(raised.exception, stream_error)
        with self.assertRaisesRegex(RuntimeError, "close failure"):
            response.close()
        self.assertEqual(self.roots[0].error, 0)
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.scopes[0].get_item("response_span").error, 1)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_close_error_finishes_response_and_request(self):
        def close():
            raise ValueError("close failure")

        @self.app.route("/")
        def index():
            response = flask.Response([b"first"])
            response.call_on_close(close)
            return response

        response = self.open_response()
        with self.assertRaisesRegex(ValueError, "close failure"):
            response.close()
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 0)
        self.assertEqual(self.scopes[0].get_item("response_span").error, 1)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_application_error_finishes_request(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = True

        @self.app.route("/")
        def index():
            raise ValueError("application failure")

        with self.assertRaisesRegex(ValueError, "application failure"):
            self.open_response()
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 1)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_application_stop_iteration_is_not_an_error(self):
        def app(environ, start_response):
            raise StopIteration

        middleware = _FlaskWSGIMiddleware(app, None, config.flask)
        with self.assertRaises(StopIteration):
            self.open_response(app=middleware)
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 0)
        self.assertEqual(self.phases.count("ended"), 1)
        self.assertTrue(all(span.finished for span in self.get_spans()))

    def test_interleaved_requests_keep_independent_contexts(self):
        seen = []

        @self.app.route("/<name>")
        def index(name):
            def generate():
                with self.tracer.trace("stream.child") as child:
                    seen.append((name, child.parent_id))
                    yield name.encode()

            return flask.Response(generate())

        first = self.open_response("/first")
        second = self.open_response("/second")
        self.assertTrue(all(root.parent_id is None for root in self.roots))
        self.assertNotEqual(self.roots[0].trace_id, self.roots[1].trace_id)
        self.assertEqual(next(second), b"second")
        self.assertEqual(next(first), b"first")
        first.close()
        second.close()
        self.assertEqual(
            seen,
            [
                ("second", self.scopes[1].get_item("response_span").span_id),
                ("first", self.scopes[0].get_item("response_span").span_id),
            ],
        )
        self.assertEqual(self.phases.count("ended"), 2)
        self.assertTrue(all(root.finished for root in self.roots))

    def test_start_response_preserves_write_callable(self):
        writes = []
        calls = []

        def start_response(status, headers):
            calls.append((status, headers))
            return writes.append

        def app(environ, respond):
            with self.app.request_context(environ):
                write = respond("200 OK", [])
                write(b"written")
            return [b"body"]

        builder = EnvironBuilder()
        self.addCleanup(builder.close)
        middleware = _FlaskWSGIMiddleware(app, None, config.flask)
        response = middleware(builder.get_environ(), start_response)
        self.addCleanup(response.close)
        self.assertEqual(list(response), [b"body"])
        self.assertEqual(calls, [("200 OK", [])])
        self.assertEqual(writes, [b"written"])

    def test_custom_resource_survives_response_completion(self):
        @self.app.route("/")
        def index():
            self.tracer.current_root_span().resource = "custom-view"
            return "ok"

        response = self.open_response()
        self.assertEqual(self.roots[0].resource, "custom-view")
        list(response)
        self.assertEqual(self.roots[0].resource, "custom-view")

    def test_streamed_custom_resource_survives_completion(self):
        @self.app.route("/")
        def index():
            def generate():
                self.tracer.current_root_span().resource = "custom-stream"
                yield b"ok"

            return flask.Response(generate())

        response = self.open_response()
        list(response)
        self.assertEqual(self.roots[0].resource, "custom-stream")

    def test_stream_with_context_can_close_early(self):
        closed = []

        @self.app.route("/")
        def index():
            @flask.stream_with_context
            def generate():
                try:
                    yield flask.request.path.encode()
                    yield b"unused"
                finally:
                    closed.append(flask.request.path)

            return flask.Response(generate())

        response = self.open_response()
        self.assertEqual(next(response), b"/")
        response.close()
        self.assertEqual(closed, ["/"])
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_application_return_precedes_stream_completion_once(self) -> None:
        returns = []
        chunks: list[str] = []

        def returned(ctx: core.ExecutionContext[WebFrameworkRequestEvent], exc_info: ExcInfoType) -> None:
            returns.append((ctx, exc_info, ctx.get_item("req_span").finished, core.current is ctx))

        core.on("wsgi.application.returned", returned)
        self.addCleanup(core.reset_listeners, "wsgi.application.returned", returned)

        @self.app.route("/")
        def index() -> flask.Response:
            def generate() -> Iterator[bytes]:
                self.assertIs(core.current, caller_context)
                chunks.append("first")
                yield b"first"
                self.assertIs(core.current, caller_context)
                chunks.append("second")
                yield b"second"

            return flask.Response(generate())

        caller_context = core.current
        response = self.open_response()
        self.assertIs(core.current, caller_context)
        self.assertEqual(chunks, [])
        self.assertEqual(returns, [(self.scopes[0], (None, None, None), False, True)])
        self.assertEqual(self.phases, ["started"])
        self.assertFalse(self.roots[0].finished)

        self.assertEqual(next(response), b"first")
        self.assertIs(core.current, caller_context)
        self.assertEqual(chunks, ["first"])
        self.assertFalse(self.roots[0].finished)
        self.assertEqual(len(returns), 1)

        self.assertEqual(list(response), [b"second"])
        response.close()
        response.close()
        self.assertIs(core.current, caller_context)
        self.assertEqual(chunks, ["first", "second"])
        self.assertEqual(len(returns), 1)
        self.assertEqual(self.phases, ["started", "ended"])
        self.assertTrue(self.roots[0].finished)

    def test_application_failure_reports_return_once(self) -> None:
        returns = []
        failure = ValueError("application failure")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True

        def returned(ctx: core.ExecutionContext[WebFrameworkRequestEvent], exc_info: ExcInfoType) -> None:
            returns.append((ctx, exc_info[0], exc_info[1], core.current is ctx))

        core.on("wsgi.application.returned", returned)
        self.addCleanup(core.reset_listeners, "wsgi.application.returned", returned)

        @self.app.route("/")
        def index() -> flask.Response:
            raise failure

        with self.assertRaises(ValueError) as raised:
            self.open_response()
        self.assertIs(raised.exception, failure)
        self.assertEqual(returns, [(self.scopes[0], ValueError, failure, True)])
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.phases.count("ended"), 1)

    def test_success_inside_handled_exception_reports_no_application_error(self) -> None:
        returns: list[ExcInfoType] = []

        def returned(ctx: core.ExecutionContext[WebFrameworkRequestEvent], exc_info: ExcInfoType) -> None:
            returns.append(exc_info)

        core.on("wsgi.application.returned", returned)
        self.addCleanup(core.reset_listeners, "wsgi.application.returned", returned)

        @self.app.route("/")
        def index() -> str:
            return "ok"

        try:
            raise ValueError("already handled by caller")
        except ValueError:
            response = self.open_response()
            self.assertEqual(list(response), [b"ok"])
        self.assertEqual(returns, [(None, None, None)])
        self.assertEqual(self.roots[0].error, 0)


@pytest.fixture
def preserved_client_app() -> Iterator[flask.Flask]:
    patch()
    try:
        yield flask.Flask(__name__)
    finally:
        unpatch()


@pytest.mark.parametrize("termination", ["exhaustion", "early_close", "stream_error"])
@pytest.mark.parametrize("outer_wsgi", [False, True])
def test_preserved_client_streaming(
    preserved_client_app: flask.Flask, tracer: Tracer, termination: str, outer_wsgi: bool
) -> None:
    app = preserved_client_app
    contexts: list[core.ExecutionContext[WebFrameworkRequestEvent]] = []
    children: list[Span] = []
    closed: list[str] = []
    failure = RuntimeError("stream failure")

    def started(ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        if ctx.event.component == "flask":
            contexts.append(ctx)

    core.on("context.started.web.request", started)

    @app.route("/stream")
    def stream() -> flask.Response:
        flask.g.message = "preserved"

        @flask.stream_with_context
        def generate() -> Iterator[bytes]:
            try:
                with tracer.trace("stream.child") as child:
                    children.append(child)
                    assert flask.request.path == "/stream"
                    assert flask.g.message == "preserved"
                    yield b"first"
                    if termination == "stream_error":
                        raise failure
                    yield b"second"
            finally:
                closed.append(flask.request.path)

        return app.response_class(generate())

    if outer_wsgi:
        app.wsgi_app = DDWSGIMiddleware(app.wsgi_app)

    caller_context = core.current
    try:
        with tracer.trace("caller") as caller:
            with app.test_client() as client:
                response = client.get("/stream", buffered=False)
                assert len(contexts) == 1
                request_span = span_from_context(contexts[0])
                response_span = contexts[0].get_item("response_span")
                assert not request_span.finished
                assert not response_span.finished
                assert flask.request.path == "/stream"
                assert flask.g.message == "preserved"
                body = iter(response.response)
                assert next(body) == b"first"
                if termination == "exhaustion":
                    assert list(body) == [b"second"]
                elif termination == "stream_error":
                    with pytest.raises(RuntimeError, match="stream failure") as raised:
                        next(body)
                    assert raised.value is failure
                response.close()
                response.close()
                assert all(span.finished for span in (request_span, response_span))
                assert request_span.error == 0
                assert response_span.error == int(termination == "stream_error")
                assert request_span.get_tag("http.status_code") == "200"
                assert response_span.parent_id == request_span.span_id
                assert children[0].trace_id == request_span.trace_id
                if not outer_wsgi:
                    assert children[0].parent_id == response_span.span_id
                assert closed == ["/stream"]
                assert flask.request.path == "/stream"
                assert flask.g.message == "preserved"
                assert tracer.current_span() is caller
            assert core.current is caller_context
            assert tracer.current_span() is caller
    finally:
        core.reset_listeners("context.started.web.request", started)


@pytest.fixture
def service_app() -> Iterator[flask.Flask]:
    patch()
    try:
        app = flask.Flask(__name__)
        app.config["PROPAGATE_EXCEPTIONS"] = True
        yield app
    finally:
        unpatch()


@pytest.mark.parametrize("terminal", ["exhaustion", "early_close", "stream_error", "application_error"])
def test_flask_service_override_survives_completion(
    service_app: flask.Flask, tracer: Any, test_spans: Any, terminal: str
) -> None:
    roots: list[Span] = []
    sources: list[Any] = []

    @service_app.route("/")
    def index() -> flask.Response:
        span = global_tracer.current_root_span()
        assert span is not None
        span.service = "custom-flask-service"
        roots.append(span)
        sources.append(span.get_tag(_SERVICE_SOURCE))
        if terminal == "application_error":
            raise RuntimeError("application failure")

        def generate() -> Iterator[bytes]:
            yield b"first"
            if terminal == "stream_error":
                raise RuntimeError("stream failure")
            yield b"second"

        return flask.Response(generate())

    builder = EnvironBuilder(path="/")
    try:
        if terminal == "application_error":
            with pytest.raises(RuntimeError, match="application failure"):
                service_app(builder.get_environ(), lambda *args: None)
        else:
            response = service_app(builder.get_environ(), lambda *args: None)
            assert roots[0].service == "custom-flask-service"
            assert next(response) == b"first"
            if terminal == "stream_error":
                with pytest.raises(RuntimeError, match="stream failure"):
                    next(response)
            elif terminal == "exhaustion":
                with override_global_config(dict(service_mapping={"custom-flask-service": "late-mapped-service"})):
                    assert list(response) == [b"second"]
            response.close()
    finally:
        builder.close()

    spans = test_spans.pop()
    request_span = roots[0]
    assert request_span.finished
    assert request_span.service == "custom-flask-service"
    assert request_span.get_tag(_SERVICE_SOURCE) == sources[0]
    assert request_span.error == int(terminal == "application_error")
    response_spans = [span for span in spans if span.name == "flask.response"]
    assert len(response_spans) == int(terminal != "application_error")
    if response_spans:
        assert response_spans[0].parent_id == request_span.span_id
        assert response_spans[0].service == request_span.service


def test_flask_configured_service_survives_completion(service_app: flask.Flask, tracer: Any, test_spans: Any) -> None:
    @service_app.route("/")
    def index() -> str:
        return "ok"

    with override_config("flask", dict(service_name="configured-flask-service")):
        response = service_app.test_client().get("/")
        assert response.get_data() == b"ok"
        response.close()

    spans = test_spans.pop()
    request_span = next(span for span in spans if span.name == "flask.request")
    response_span = next(span for span in spans if span.name == "flask.response")
    assert request_span.service == response_span.service == "configured-flask-service"
    assert response_span.parent_id == request_span.span_id


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_manual_flask_with_deferred_tracer_initialization() -> None:
    import sys
    from typing import Any
    from typing import cast

    import ddtrace  # noqa: F401

    assert "ddtrace.trace" not in sys.modules
    assert "ddtrace._trace.subscribers" not in sys.modules
    from flask import Flask
    from flask import Response
    from flask import request
    from flask import stream_with_context
    from werkzeug.test import EnvironBuilder

    from ddtrace import patch
    from ddtrace.internal.products import manager
    from ddtrace.trace import tracer
    from tests.utils import DummyWriter

    patch(flask=True)
    assert manager._products is None
    app = Flask(__name__)

    @app.route("/stream")
    def stream():
        @stream_with_context
        def body():
            assert request.path == "/stream"
            yield b"first"
            yield b"second"

        return Response(body())

    builder = EnvironBuilder(path="/stream")
    try:
        writer = DummyWriter()
        tracer._span_aggregator.writer = cast(Any, writer)
        with tracer.trace("caller") as caller:
            response = app(builder.get_environ(), lambda *args: lambda data: None)
            assert tracer.current_root_span() is caller
            assert list(response) == [b"first", b"second"]
            response.close()
            assert tracer.current_span() is caller
        spans = [span for trace in writer.pop_traces() for span in trace]
        (root,) = [span for span in spans if span.name == "flask.request"]
        assert root.parent_id == caller.span_id
        for name in ("flask.application", "flask.response"):
            (child,) = [span for span in spans if span.name == name]
            assert child.parent_id == root.span_id
        assert all(span.finished for span in spans)
    finally:
        tracer.shutdown()
        builder.close()
    assert manager._products is None


def test_nested_wsgi_flask_interleaved_request_roots(
    preserved_client_app: flask.Flask, tracer: Tracer, test_spans: Any
) -> None:
    app = preserved_client_app
    contexts: list[Any] = []
    responses: list[Any] = []

    def started(ctx: Any) -> None:
        contexts.append(ctx)

    @app.route("/stream")
    def stream() -> flask.Response:
        return flask.Response(iter([b"one", b"two"]))

    app.wsgi_app = DDWSGIMiddleware(app.wsgi_app)
    core.on("context.started.web.request", started)
    builder = EnvironBuilder(path="/stream")
    try:
        for _ in range(2):
            responses.append(app(builder.get_environ(), lambda *args: None))
        outer_first, inner_first, outer_second, inner_second = contexts
        roots = [span_from_context(ctx) for ctx in contexts]
        assert roots[0].parent_id is None and roots[2].parent_id is None
        assert roots[0].trace_id != roots[2].trace_id
        for outer, inner in ((outer_first, inner_first), (outer_second, inner_second)):
            assert span_from_context(inner).parent_id == outer.get_item("app_span").span_id
            assert span_from_context(inner).trace_id == span_from_context(outer).trace_id
        for response in reversed(responses):
            assert list(response) == [b"one", b"two"]
            response.close()
        assert all(span.finished for span in test_spans.pop())
    finally:
        for response in responses:
            response.close()
        builder.close()
        core.reset_listeners("context.started.web.request", started)
