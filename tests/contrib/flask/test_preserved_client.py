from typing import Iterator

import flask
import pytest

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.flask.patch import patch
from ddtrace.contrib.internal.flask.patch import unpatch
from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import Span
from ddtrace.trace import Tracer


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
        contexts.append(ctx)

    core.on("span.started.flask.request", started)

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
        core.reset_listeners("span.started.flask.request", started)
