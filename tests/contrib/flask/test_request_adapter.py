"""Exercise Flask callbacks through the shared blocking protocol, without patch wiring."""

from collections.abc import Callable
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from unittest.mock import call

import flask
import pytest

from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.contrib.internal.flask.request import complete_response
from ddtrace.contrib.internal.flask.request import execute_response
from ddtrace.contrib.internal.flask.request import prepare_request
from ddtrace.contrib.internal.flask.request import update_route
from ddtrace.contrib.internal.web_framework import run_web_framework_request
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig


@pytest.fixture
def adapter_app() -> flask.Flask:
    class CustomResponse(flask.Response):
        pass

    app = flask.Flask(__name__)
    app.response_class = CustomResponse
    app.add_url_rule("/items/<int:item_id>", view_func=lambda item_id: "ok")
    return app


@pytest.fixture
def inspection() -> Iterator[tuple[dict[str, Any], list[Any]]]:
    policy: dict[str, Any] = {}
    seen: list[Any] = []

    class Observer(Subscriber):
        auto_register = False
        event_names = (
            WebFrameworkRequestInspectEvent.event_name,
            WebFrameworkResponseInspectEvent.event_name,
            WebFrameworkBlockResponseEvent.event_name,
        )

        @classmethod
        def on_event(cls, notification: Any) -> None:
            if isinstance(notification, WebFrameworkBlockResponseEvent):
                seen.append(("block", notification.block))
                notification.response = policy.get("replacement")
            elif isinstance(notification, WebFrameworkRequestInspectEvent):
                seen.append(
                    ("request", notification.peer_ip, dict(notification.cookies), dict(notification.path_params))
                )
                if policy.get("phase") == "request":
                    raise BlockingException(SimpleNamespace(status_code=403))
            elif isinstance(notification, WebFrameworkResponseInspectEvent):
                seen.append(("response", notification.request_event.response_status_code))
                if policy.get("phase") == "response":
                    raise BlockingException(SimpleNamespace(status_code=403))

    Observer.register()
    try:
        yield policy, seen
    finally:
        Observer.unregister()


def run_response(app: flask.Flask, callback: Callable[[], flask.Response], path: str = "/items/42") -> tuple[Any, Any]:
    # This synthetic response-producing callback is not a proposed Flask hook:
    # production dispatch/finalization and WSGI lifetime still need an owner.
    with app.test_request_context(
        path, headers={"Cookie": "session=abc"}, environ_overrides={"REMOTE_ADDR": "192.0.2.1"}
    ):
        integration_config = IntegrationConfig(Config(), "flask", _default_service="flask")
        event = prepare_request(flask.request.environ, integration_config)
        update_route(event, flask.request)
        result = run_web_framework_request(
            callback, (), {}, prepare=lambda: event, complete=complete_response, execute=execute_response
        )
        return result, event


@pytest.mark.parametrize("phase", ["request", "response"])
@pytest.mark.parametrize(
    "replacement",
    [
        (403, {"Content-Type": "application/json"}, b'{"blocked":true}'),
        (403, {"Content-Type": "text/html"}, b"<p>blocked</p>"),
        (302, {"Location": "/blocked"}, b""),
    ],
)
def test_shared_adapter_replaces_flask_response(
    adapter_app: flask.Flask, inspection: Any, phase: str, replacement: Any
) -> None:
    policy, seen = inspection
    policy.update(phase=phase, replacement=replacement)
    body = Mock()
    original = adapter_app.response_class(body, headers={"X-Application": "original"})
    on_close = Mock()
    original.call_on_close(on_close)
    callback = Mock(return_value=original)

    result, event = run_response(adapter_app, callback)

    assert isinstance(result, adapter_app.response_class)
    assert result is not original
    assert (result.status_code, result.get_data()) == (replacement[0], replacement[2])
    for name, value in replacement[1].items():
        assert result.headers[name] == value
    assert "X-Application" not in result.headers
    assert "X-Application" not in event.response_headers
    assert event.response_status_code == replacement[0]
    assert not event.response_from_exception
    assert callback.call_count == int(phase == "response")
    assert seen[0] == ("request", "192.0.2.1", {"session": "abc"}, {"item_id": 42})
    assert seen[1] == ("response", 200 if phase == "response" else None)
    assert len(seen) == 3
    assert body.mock_calls == ([call.close()] if phase == "response" else [])
    assert on_close.call_count == int(phase == "response")
    result.close()


def test_unblocked_response_is_unchanged_and_unconsumed(adapter_app: flask.Flask, inspection: Any) -> None:
    _, seen = inspection
    body = Mock()
    original = adapter_app.response_class(body, status=201)
    on_close = Mock()
    original.call_on_close(on_close)
    result, event = run_response(adapter_app, lambda: original)
    assert result is original
    assert result.response is body
    assert event.response_status_code == 201
    assert body.mock_calls == []
    on_close.assert_not_called()
    assert seen[-1] == ("response", 201)
    result.close()
    body.close.assert_called_once_with()
    on_close.assert_called_once_with()


def test_shared_adapter_uses_default_block_content(adapter_app: flask.Flask, inspection: Any) -> None:
    policy, _ = inspection
    policy["phase"] = "request"
    callback = Mock()
    result, event = run_response(adapter_app, callback)
    assert isinstance(result, adapter_app.response_class)
    assert result.status_code == event.response_status_code == 403
    assert result.mimetype == "application/json"
    assert result.get_json()
    assert int(result.headers["Content-Length"]) == len(result.get_data())
    callback.assert_not_called()
    result.close()


@pytest.mark.parametrize("path", ["/missing", "/items/42"])
def test_adapter_has_no_cross_request_state(adapter_app: flask.Flask, inspection: Any, path: str) -> None:
    policy, seen = inspection
    policy["phase"] = "request"
    blocked, _ = run_response(adapter_app, Mock())
    blocked.close()
    policy.clear()
    seen.clear()
    original = adapter_app.response_class("ok")
    result, event = run_response(adapter_app, lambda: original, path=path)
    assert result is original
    assert event.response_status_code == 200
    assert seen[0][3] == ({} if path == "/missing" else {"item_id": 42})
    assert len(seen) == 2
    result.close()


@pytest.mark.parametrize("error", [ValueError("application failure"), KeyboardInterrupt()])
def test_adapter_preserves_non_blocking_exceptions(
    adapter_app: flask.Flask, inspection: Any, error: BaseException
) -> None:
    _, seen = inspection
    callback = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        run_response(adapter_app, callback)
    assert caught.value is error
    callback.assert_called_once_with()
    assert seen[-1] == ("response", None)
    assert len(seen) == 2


def test_discard_close_failure_does_not_replace_block_response(adapter_app: flask.Flask, inspection: Any) -> None:
    policy, _ = inspection
    policy.update(phase="response", replacement=(403, {"Content-Type": "text/plain"}, b"blocked"))
    body = Mock()
    body.close.side_effect = ValueError("close failed")
    original = adapter_app.response_class(body)
    result, event = run_response(adapter_app, lambda: original)
    assert result.get_data() == b"blocked"
    assert event.response_status_code == 403
    body.close.assert_called_once_with()
    result.close()


def test_blocking_adapter_runs_with_tracing_disabled(
    adapter_app: flask.Flask, inspection: Any, tracer: Any, test_spans: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracer, "enabled", False)
    policy, seen = inspection
    policy["phase"] = "request"
    callback = Mock()
    result, event = run_response(adapter_app, callback)
    assert result.status_code == event.response_status_code == 403
    assert [entry[0] for entry in seen] == ["request", "response", "block"]
    callback.assert_not_called()
    assert test_spans.pop() == []
    assert tracer.current_span() is None
    result.close()
