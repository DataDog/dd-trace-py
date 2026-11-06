from contextlib import ExitStack
from unittest import mock

import pytest

from ddtrace import config as trace_config
from ddtrace._trace.subscribers.http_client import HttpClientTracingSubscriber
from ddtrace.appsec import _listeners
from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber
from ddtrace.appsec._contrib.filesystem.subscribers import AppSecFileOpenSubscriber
from ddtrace.appsec._contrib.httpx import subscribers as httpx_subscribers
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber
from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.internal import core
from ddtrace.internal.appsec import product
from ddtrace.internal.appsec import product_subscribers
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.span_bus import span_from_context


EXPLOIT_PREVENTION_SUBSCRIBERS = (
    AppSecDbApiSubscriber,
    AppSecFileOpenSubscriber,
    AppSecHttpxRequestContextSubscriber,
    AppSecHttpxSingleRequestContextSubscriber,
    AppSecSubprocessCommandSubscriber,
)


@pytest.fixture
def subscribers(monkeypatch):
    was_enabled = asm_config._asm_enabled
    monkeypatch.setattr(asm_config, "_asm_can_be_enabled", True)
    monkeypatch.setattr(asm_config, "_asm_enabled_origin", asm_config._asm_enabled_origin)
    _listeners.disable_appsec()
    with ExitStack() as stack:
        yield {
            subscriber: (
                stack.enter_context(mock.patch.object(subscriber, "register")),
                stack.enter_context(mock.patch.object(subscriber, "unregister")),
            )
            for subscriber in EXPLOIT_PREVENTION_SUBSCRIBERS
        }
        _listeners.disable_appsec()
    if was_enabled:
        _listeners.load_appsec()


def test_remote_activation_registers_exploit_prevention_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        product._enable_asm()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_not_called()

        product._disable_asm()

        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_called_once_with()


def test_remote_activation_without_exploit_prevention_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", False):
        product._enable_asm()
        product._disable_asm()

    for register, _ in subscribers.values():
        register.assert_not_called()


def test_direct_appsec_activation_registers_exploit_prevention_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        _listeners.load_appsec()

    for register, unregister in subscribers.values():
        register.assert_called_once_with()
        unregister.assert_not_called()


def test_inactive_appsec_registers_no_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        product_subscribers.register()

    for register, _ in subscribers.values():
        register.assert_not_called()


def test_asm_switch_state_registers_and_unregisters_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        asm_config._asm_enabled = True
        _listeners._asm_switch_state()
        for register, unregister in subscribers.values():
            register.assert_called_once_with()
            unregister.assert_not_called()

        asm_config._asm_enabled = False
        _listeners._asm_switch_state()
        for _, unregister in subscribers.values():
            unregister.assert_called_once_with()


def test_direct_disable_unregisters_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        _listeners.load_appsec()
        _listeners.disable_appsec()
    for _, unregister in subscribers.values():
        unregister.assert_called_once_with()


def test_product_stop_unregisters_subscribers(subscribers):
    with mock.patch.object(asm_config, "_ep_enabled", True):
        _listeners.load_appsec()
        product.stop()
    for _, unregister in subscribers.values():
        unregister.assert_called_once_with()


def test_registration_is_idempotent_and_restores_reset_listeners(monkeypatch):
    was_enabled = asm_config._asm_enabled
    with (
        mock.patch.object(asm_config, "_asm_enabled", True),
        mock.patch.object(asm_config, "_ep_enabled", True),
    ):
        handler = mock.Mock()
        monkeypatch.setattr(AppSecDbApiSubscriber, "_event_handlers", (handler,))
        try:
            product_subscribers.register()
            product_subscribers.register()
            event = DbQueryEvent(query="SELECT 1", span_name_prefix="sqlite")
            core.dispatch_event(event)
            handler.assert_called_once_with(event)

            AppSecDbApiSubscriber.unregister()
            product_subscribers.register()
            core.dispatch_event(event)
            assert handler.call_count == 2
        finally:
            product_subscribers.unregister()
    if was_enabled:
        product_subscribers.register()


@pytest.mark.parametrize("shutdown", [product.stop, _listeners.disable_appsec])
@pytest.mark.parametrize("raises", [False, True])
def test_shutdown_during_httpx_request_preserves_completion(shutdown, raises):
    was_enabled = asm_config._asm_enabled
    parent = core.current
    error = ValueError("application failure")
    event = HttpClientRequestEvent(
        http_operation="http.request",
        service=None,
        component="httpx",
        integration_config=trace_config.httpx,
        request_method="GET",
        request_headers={},
        request_url="https://example.test/",
    )
    event.event_name = HttpClientEvents.HTTPX_REQUEST.value
    HttpClientTracingSubscriber.register()
    with (
        mock.patch.object(asm_config, "_asm_enabled", True),
        mock.patch.object(asm_config, "_ep_enabled", True),
        mock.patch.object(httpx_subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(httpx_subscribers, "_get_asm_context", return_value=mock.Mock()),
        mock.patch.object(httpx_subscribers, "should_analyze_body_response", return_value=False),
    ):
        product_subscribers.register()
        try:
            try:
                with core.context_with_event(event) as ctx:
                    span = span_from_context(ctx)
                    assert not span.finished
                    assert core.find_item("asm::rasp_subcontext") is not None
                    shutdown()
                    if raises:
                        raise error
            except ValueError as caught:
                assert raises
                assert caught is error
            else:
                assert not raises
            assert span.finished
            assert span.error == int(raises)
            assert core.current is parent
            assert core.find_item("asm::rasp_subcontext") is None
        finally:
            product_subscribers.unregister()
    if was_enabled:
        product_subscribers.register()
