from collections.abc import Iterator
from typing import Any

import flask
import pytest
from werkzeug.test import EnvironBuilder

from ddtrace.contrib.internal.flask.patch import patch
from ddtrace.contrib.internal.flask.patch import unpatch
from ddtrace.internal.constants import _SERVICE_SOURCE
from ddtrace.trace import Span
from ddtrace.trace import tracer as global_tracer
from tests.utils import override_config
from tests.utils import override_global_config


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
