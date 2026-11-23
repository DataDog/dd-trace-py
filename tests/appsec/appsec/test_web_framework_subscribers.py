from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import cast
from unittest.mock import Mock
from unittest.mock import call

import pytest

from ddtrace.appsec import _asm_request_context as asm
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._contrib.web_framework.subscribers import AppSecWebFrameworkSubscriber
from ddtrace.appsec._utils import Block_config
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib.internal.web_framework import RequestInspectionDetails
from ddtrace.contrib.internal.web_framework import blocking_execute_adapter
from ddtrace.contrib.internal.web_framework import run_web_framework_request
from ddtrace.internal import core
from ddtrace.internal.appsec import product_subscribers
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils import Block_config as BlockConfigProtocol
from tests.appsec import rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import is_blocked


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]
    body: Any


def complete(event: WebFrameworkRequestEvent, result: Any, error: Optional[BaseException]) -> None:
    event.response_status_code = result.status if isinstance(result, Response) else None
    event.response_headers = result.headers if isinstance(result, Response) else {}


execute = blocking_execute_adapter(
    request_details=lambda kwargs: RequestInspectionDetails("192.0.2.1", {"session": "abc"}, kwargs),
    is_response_object=lambda result: isinstance(result, Response),
    extract_body=lambda result: result.body,
    build_response=Response,
)


def request_event(component: str = "framework") -> WebFrameworkRequestEvent:
    return WebFrameworkRequestEvent(
        http_operation=f"{component}.request",
        component=component,
        integration_config=IntegrationConfig(Config(), component),
        service=None,
        request_method="GET",
        request_url="http://example.test/items/42?search=one&search=two&empty=",
        request_headers={"Accept": "text/html"},
        query="search=one&search=two&empty=",
        request_route="/items/{item_id}",
        headers_case_sensitive=True,
    )


def run(event: WebFrameworkRequestEvent, callback: Any) -> Any:
    return run_web_framework_request(
        callback, (), {"item_id": "42"}, prepare=lambda: event, complete=complete, execute=execute
    )


@pytest.fixture(autouse=True)
def restore_registration(tracer: Any) -> Iterator[None]:
    yield
    product_subscribers.unregister()
    if asm_config._asm_enabled:
        product_subscribers.register()


@pytest.mark.parametrize("component", ["flask", "bottle", "another_framework"])
@pytest.mark.parametrize("phase", ["request", "response", "sdk"])
def test_shared_blocking_for_any_framework(component: str, phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    event = request_event(component)
    body = Mock()
    response = Response(200, {"X-Application": "original"}, body)
    callback = Mock(return_value=response)
    with asm_context(config={"_asm_enabled": True, "_ep_enabled": False}):
        environment = asm.get_active_asm_context()
        assert environment is not None
        original_block = Block_config(status_code=429)
        calls = []

        def evaluate(*args: Any, **kwargs: Any) -> None:
            assert asm.get_active_asm_context() is environment
            status = asm.get_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS)
            calls.append(status)
            if (phase == "request" and status is None) or (phase == "response" and status == "200"):
                asm.set_blocked(original_block)

        monkeypatch.setattr(asm, "call_waf_callback", evaluate)
        if phase == "sdk":
            callback.side_effect = lambda **kwargs: asm.block_request()
        result = run(event, callback)
        assert result.status == event.response_status_code == (403 if phase == "sdk" else 429)
        assert "X-Application" not in result.headers
        assert result.headers["Content-Type"] == "text/html"
        assert int(result.headers["Content-Length"]) == len(result.body)
        assert asm.get_active_asm_context() is environment
        assert asm.get_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS) == str(result.status)
        assert asm.get_waf_address(SPAN_DATA_NAMES.REQUEST_QUERY) == {"search": ["one", "two"], "empty": [""]}
        assert asm.get_waf_address(SPAN_DATA_NAMES.REQUEST_COOKIES) == {"session": "abc"}
        assert asm.get_waf_address(SPAN_DATA_NAMES.REQUEST_PATH_PARAMS) == {"item_id": "42"}
        assert asm.get_ip() == "192.0.2.1"
        assert callback.call_count == int(phase != "request")
        assert body.mock_calls == ([] if phase != "response" else [call.close()])
        assert calls


@pytest.mark.parametrize("phase", ["request", "response"])
def test_shared_subscriber_uses_real_waf(phase: str) -> None:
    event = request_event()
    settings: dict[str, Any] = {"_asm_enabled": True, "_ep_enabled": False}
    if phase == "request":
        event.request_headers["User-Agent"] = "dd-test-scanner-log-block"
    else:
        settings["_asm_static_rule_file"] = rules.RULES_SRB_RESPONSE
    body = Mock()
    callback = Mock(return_value=Response(404, {}, body))
    with asm_context(config=settings) as span:
        result = run(event, callback)
        assert result.status == event.response_status_code == 403
        assert result.body
        assert asm.get_blocked() is not None
        assert callback.call_count == int(phase == "response")
        assert body.mock_calls == ([call.close()] if phase == "response" else [])
    assert is_blocked(span)
    assert get_triggers(span)


@pytest.mark.parametrize("block_type", ["json", "html", "none"])
def test_block_rendering_honors_configuration(block_type: str) -> None:
    with asm_context(config={"_asm_enabled": True, "_ep_enabled": False}):
        block = Block_config(type=block_type, status_code=307 if block_type == "none" else 451, location="/blocked")
        asm.set_blocked(block)
        # The legacy shared protocol's get() annotation excludes None; rendering reads only attributes.
        notification = WebFrameworkBlockResponseEvent(cast(BlockConfigProtocol, block))
        core.dispatch_event(notification)
        assert notification.response is not None
        status, headers, body = notification.response
        assert status == block.status_code
        assert int(headers["Content-Length"]) == len(body)
        if block_type == "none":
            assert body == b""
            assert headers["Location"] == "/blocked"
        else:
            assert headers["Content-Type"] == ("text/html" if block_type == "html" else "application/json")
            assert body


def test_inactive_appsec_does_not_inspect_or_replace_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asm_config, "_asm_enabled", False)
    AppSecWebFrameworkSubscriber.register()
    evaluate = Mock()
    monkeypatch.setattr(asm, "call_waf_callback", evaluate)
    body = Mock()
    response = Response(201, {"X-Application": "kept"}, body)
    event = request_event()
    assert run(event, Mock(return_value=response)) is response
    assert event.response_status_code == 201
    evaluate.assert_not_called()
    assert body.mock_calls == []


def test_inspection_without_asm_context_does_not_run_waf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asm_config, "_asm_enabled", True)
    evaluate = Mock()
    monkeypatch.setattr(asm, "call_waf_callback", evaluate)
    AppSecWebFrameworkSubscriber.on_event(WebFrameworkRequestInspectEvent(request_event(), None, {}, {}))
    evaluate.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("exploit_prevention", [False, True])
def test_registration_is_owned_by_appsec(
    enabled: bool, exploit_prevention: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    product_subscribers.unregister()
    monkeypatch.setattr(asm_config, "_asm_enabled", enabled)
    monkeypatch.setattr(asm_config, "_ep_enabled", exploit_prevention)
    handler = Mock()
    monkeypatch.setattr(AppSecWebFrameworkSubscriber, "_event_handlers", (handler,))
    product_subscribers.register()
    product_subscribers.register()
    notification = WebFrameworkBlockResponseEvent(cast(BlockConfigProtocol, Block_config()))
    core.dispatch_event(notification)
    assert handler.call_count == int(enabled)
    AppSecWebFrameworkSubscriber.unregister()
    product_subscribers.register()
    core.dispatch_event(notification)
    assert handler.call_count == 2 * int(enabled)
    product_subscribers.unregister()
    core.dispatch_event(notification)
    assert handler.call_count == 2 * int(enabled)


@pytest.mark.parametrize("outcome", ["success", "error", "block"])
def test_adapter_restores_outer_sdk_callback(outcome: str) -> None:
    previous = Mock()
    event = request_event()
    with asm_context(config={"_asm_enabled": True, "_ep_enabled": False}, block_request_callable=previous):
        environment = asm.get_active_asm_context()
        assert environment is not None
        callback = Mock(return_value=Response(200, {}, b"ok"))
        if outcome == "error":
            callback.side_effect = ValueError("application failure")
            with pytest.raises(ValueError, match="application failure"):
                run(event, callback)
        else:
            if outcome == "block":
                callback.side_effect = lambda **kwargs: asm.block_request()
            result = run(event, callback)
            assert result.status == (403 if outcome == "block" else 200)
        assert environment.block_callable is previous
        asm.block_request()
        previous.assert_called_once_with()
