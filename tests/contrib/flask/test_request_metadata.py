"""Flask metadata extraction does not execute application bodies or manage contexts."""

from io import BytesIO
from typing import Any
from typing import cast
from unittest.mock import Mock

import flask
import pytest
from werkzeug.datastructures import Headers
from werkzeug.exceptions import NotFound
from werkzeug.test import EnvironBuilder

from ddtrace.contrib.internal.flask.request import complete_response
from ddtrace.contrib.internal.flask.request import prepare_request
from ddtrace.contrib.internal.flask.request import update_route
from ddtrace.internal import core
from ddtrace.internal.constants import FLASK_ENDPOINT
from ddtrace.internal.constants import FLASK_RESOURCE_FULL
from ddtrace.internal.constants import FLASK_URL_RULE
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig


@pytest.fixture
def metadata_config() -> IntegrationConfig:
    return IntegrationConfig(Config(), "flask", _default_service="flask", distributed_tracing_enabled=True)


@pytest.mark.parametrize("raw_uri", [None, "/mount/items/a%2Fb?q=one", "/items/a%2Fb?q=one"])
def test_prepare_wsgi_metadata_without_context(metadata_config: IntegrationConfig, raw_uri: Any) -> None:
    builder = EnvironBuilder(
        path="/items/a%2Fb?q=one",
        base_url="https://example.com:8443/mount/",
        method="POST",
        data=b"unread",
        headers={"X-Datadog-Trace-Id": "123", "Content-Type": "application/octet-stream"},
    )
    try:
        environ = builder.get_environ()
        environ["PATH_INFO"] = "/items/a/b"
        environ.pop("RAW_URI", None)
        environ.pop("REQUEST_URI", None)
        if raw_uri is not None:
            environ["RAW_URI"] = raw_uri
        input_stream = Mock(wraps=BytesIO(b"unread"))
        environ["wsgi.input"] = input_stream
        previous_context = core.current
        assert not flask.has_request_context()
        event = prepare_request(environ, metadata_config)
        expected_path = "/items/a/b" if raw_uri is None else "/items/a%2Fb"
        assert event.request_url == f"https://example.com:8443/mount{expected_path}?q=one"
        assert event.request_method == "POST"
        assert event.query == "q=one"
        assert event.request_headers["X-Datadog-Trace-Id"] == "123"
        assert event.request_headers["Content-Type"] == "application/octet-stream"
        assert event.request_headers["Content-Length"] == "6"
        assert event.service == "flask"
        assert event.integration_config is metadata_config
        assert event.resource is event.request_route is None
        assert event.allow_default_resource
        assert event.activate_distributed_headers
        assert core.current is previous_context
        assert input_stream.mock_calls == []
    finally:
        builder.close()


@pytest.mark.parametrize("distributed_tracing", [False, True])
def test_prepare_keeps_integration_configuration(metadata_config: IntegrationConfig, distributed_tracing: bool) -> None:
    metadata_config["service"] = "custom-flask"
    metadata_config["distributed_tracing_enabled"] = distributed_tracing
    builder = EnvironBuilder()
    try:
        event = prepare_request(builder.get_environ(), metadata_config)
        assert event.service == "custom-flask"
        # The tracing subscriber applies this setting when activating headers later.
        assert event.integration_config.distributed_tracing_enabled is distributed_tracing
        assert event.distributed_headers_config_override is None
    finally:
        builder.close()


def test_route_metadata_preserves_app_local_resource(metadata_config: IntegrationConfig) -> None:
    app = flask.Flask(__name__)
    app.add_url_rule("/items/<int:item_id>", endpoint="items", view_func=lambda item_id: "ok")
    with app.test_request_context("/items/42", base_url="http://localhost/mount/"):
        event = prepare_request(flask.request.environ, metadata_config)
        update_route(event, flask.request)
        assert event.request_route == "/items/<int:item_id>"
        assert event.resource == "GET /items/<int:item_id>"
        assert event.additional_tags == {
            FLASK_ENDPOINT: "items",
            FLASK_URL_RULE: "/items/<int:item_id>",
            FLASK_RESOURCE_FULL: "GET /mount/items/<int:item_id>",
        }


@pytest.mark.parametrize("path,method,status", [("/unknown", "GET", 404), ("/", "POST", 405)])
def test_missing_route_leaves_resource_for_status_fallback(
    metadata_config: IntegrationConfig, path: str, method: str, status: int
) -> None:
    app = flask.Flask(__name__)
    app.add_url_rule("/", view_func=lambda: "ok")
    with app.test_request_context(path, method=method):
        event = prepare_request(flask.request.environ, metadata_config)
        update_route(event, flask.request)
        response = app.full_dispatch_request()
        complete_response(event, response, None)
        assert event.response_status_code == status
        assert event.resource is event.request_route is None
        assert event.additional_tags == {}
        assert event.allow_default_resource


@pytest.mark.parametrize("status", [200, 404, 500])
def test_completion_preserves_headers_and_does_not_touch_stream(
    metadata_config: IntegrationConfig, status: int
) -> None:
    app = flask.Flask(__name__)
    body = Mock()
    response = app.response_class(body, status=status, headers=[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
    with app.test_request_context("/"):
        event = prepare_request(flask.request.environ, metadata_config)
    # Completion must work after Flask's request context has gone away.
    complete_response(event, response, None)
    assert event.response_status_code == status
    assert cast(Headers, event.response_headers).getlist("Set-Cookie") == ["a=1", "b=2"]
    assert response.headers.getlist("Set-Cookie") == ["a=1", "b=2"]
    response.headers.add("Set-Cookie", "c=3")
    assert cast(Headers, event.response_headers).getlist("Set-Cookie") == ["a=1", "b=2"]
    assert body.mock_calls == []
    assert response.response is body
    assert not event.response_from_exception


@pytest.mark.parametrize("error", [ValueError("failure"), NotFound(), KeyboardInterrupt()])
def test_completion_without_response_does_not_invent_http_status(
    metadata_config: IntegrationConfig, error: BaseException
) -> None:
    app = flask.Flask(__name__)
    with app.test_request_context("/"):
        event = prepare_request(flask.request.environ, metadata_config)
    complete_response(event, app.response_class("old", status=201), None)
    complete_response(event, None, error)
    assert event.response_status_code is None
    assert event.response_headers == {}
    assert not event.response_from_exception
    replacement = app.response_class("blocked", status=403)
    complete_response(event, replacement, None)
    assert event.response_status_code == 403
