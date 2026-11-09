from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from ddtrace import config
from ddtrace._trace._registration import STANDALONE_WEB_FRAMEWORK_SUBSCRIBERS
from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
from ddtrace._trace.subscribers import web_framework
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import requires_subscribers
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.settings.standalone import standalone_config


@pytest.fixture
def web_policy(tracer, monkeypatch):
    monkeypatch.setitem(core.root._data, "tracer", tracer)
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", True)
    monkeypatch.setattr(tracer, "enabled", True)
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    yield tracer
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    STANDALONE_WEB_FRAMEWORK_SUBSCRIBERS.unregister()


def request_event():
    return WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(config, "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )


def test_disabled_bundle_stays_closed_during_setup_and_product_start(web_policy, monkeypatch, test_spans):
    from ddtrace._trace import product

    WEB_FRAMEWORK_SUBSCRIBERS.unregister()

    @requires_subscribers(WEB_FRAMEWORK_SUBSCRIBERS)
    def setup():
        return "installed"

    monkeypatch.setattr(product, "subscriber_bundles", (WEB_FRAMEWORK_SUBSCRIBERS,))
    for _ in range(2):
        assert setup() == "installed"
        product.start()
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")
        with core.context_with_event(request_event()) as ctx:
            assert ctx.get_item("_inner_span") is None
    test_spans.assert_span_count(0)
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    with core.context_with_event(request_event()) as ctx:
        span = ctx.get_item("_inner_span")
    assert span.finished
    test_spans.assert_span_count(1)


def test_unregister_drains_a_deferred_request_without_admitting_new_spans(web_policy, test_spans):
    event = request_event()
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        span = ctx.get_item("_inner_span")
        WEB_FRAMEWORK_SUBSCRIBERS.unregister()
        WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
        assert not core.has_listeners("context.started.web.request")
        assert core.has_listeners("context.ended.web.request")
        with core.context_with_event(request_event()) as skipped:
            assert skipped.get_item("_inner_span") is None
        assert not span.finished
    event.response_status_code = 201
    ctx.dispatch_ended_event()
    ctx.dispatch_ended_event()
    assert span.finished
    assert span.get_tag("http.status_code") == "201"
    assert not core.has_listeners("context.ended.web.request")
    test_spans.assert_span_count(1)


def test_runtime_disable_and_reenable_preserve_inflight_ownership(web_policy, test_spans):
    with core.context_with_event(request_event()) as ctx:
        first = ctx.get_item("_inner_span")
        web_policy.enabled = False
        assert not core.has_listeners("context.started.web.request")
        assert core.has_listeners("context.ended.web.request")
        with core.context_with_event(request_event()) as skipped:
            assert skipped.get_item("_inner_span") is None
    assert first.finished
    assert not core.has_listeners("context.ended.web.request")
    web_policy.enabled = True
    with core.context_with_event(request_event()) as ctx:
        second = ctx.get_item("_inner_span")
    assert second.finished
    assert first is not second
    # Finishing while tracer.enabled is false preserves the existing export policy.
    test_spans.assert_span_count(1)


def test_standalone_span_owner_survives_apm_unregister(web_policy, monkeypatch):
    monkeypatch.setattr(asm_config, "_asm_enabled", True)
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", False)
    web_policy.enabled = False
    WEB_FRAMEWORK_SUBSCRIBERS.unregister()
    WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
    event = request_event()
    with core.context_with_event(event) as ctx:
        span = ctx.get_item("_inner_span")
        assert span is not None
        assert event.span_creation_enabled
        assert not web_policy.enabled
    assert span.finished
    assert not WEB_FRAMEWORK_SUBSCRIBERS.enabled


def test_unregister_during_span_start_is_nonblocking(web_policy, monkeypatch, test_spans):
    entered = Event()
    resume = Event()
    original = web_framework._start_span

    def start(ctx):
        entered.set()
        assert resume.wait(5)
        return original(ctx)

    monkeypatch.setattr(web_framework, "_start_span", start)

    def request():
        with core.context_with_event(request_event()) as ctx:
            span = ctx.get_item("_inner_span")
        return span

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(request)
        try:
            assert entered.wait(5)
            WEB_FRAMEWORK_SUBSCRIBERS.unregister()
            assert not core.has_listeners("context.started.web.request")
            assert core.has_listeners("context.ended.web.request")
        finally:
            resume.set()
        assert pending.result(timeout=5).finished
    assert not core.has_listeners("context.ended.web.request")
    test_spans.assert_span_count(1)


def test_concurrent_completion_cannot_drain_another_request(web_policy, monkeypatch, test_spans):
    with core.context_with_event(request_event(), dispatch_end_event=False) as first:
        first_span = first.get_item("_inner_span")
    with core.context_with_event(request_event(), dispatch_end_event=False) as second:
        second_span = second.get_item("_inner_span")
    WEB_FRAMEWORK_SUBSCRIBERS.unregister()
    entered = Event()
    resume = Event()
    original = web_framework.WebFrameworkRequestSubscriber._ended_handlers

    def finish(ctx, exc_info):
        if ctx is first:
            entered.set()
            assert resume.wait(5)
        for handler in original:
            handler(ctx, exc_info)

    monkeypatch.setattr(web_framework.WebFrameworkRequestSubscriber, "_ended_handlers", (finish,))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(first.dispatch_ended_event)
        try:
            assert entered.wait(5)
            first.dispatch_ended_event()
            assert core.has_listeners("context.ended.web.request")
        finally:
            resume.set()
        pending.result(timeout=5)
    assert first_span.finished
    assert not second_span.finished
    assert core.has_listeners("context.ended.web.request")
    second.dispatch_ended_event()
    assert second_span.finished
    assert not core.has_listeners("context.ended.web.request")
    test_spans.assert_span_count(2)


def test_startup_failure_finishes_the_span_already_stored_on_context(web_policy, monkeypatch, test_spans):
    original = web_framework._start_span
    started = []

    class Interrupt(BaseException):
        pass

    def start(ctx):
        started.append(original(ctx))
        raise Interrupt()

    monkeypatch.setattr(web_framework, "_start_span", start)
    with pytest.raises(Interrupt):
        with core.context_with_event(request_event()):
            pytest.fail("request must not start after startup failure")
    assert started[0].finished
    WEB_FRAMEWORK_SUBSCRIBERS.unregister()
    assert not core.has_listeners("context.ended.web.request")
    test_spans.assert_span_count(1)


def test_disabling_last_standalone_product_closes_admission(web_policy, monkeypatch):
    monkeypatch.setattr(asm_config, "_asm_enabled", True)
    monkeypatch.setattr(asm_config, "_iast_enabled", False)
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", False)
    web_policy.enabled = False
    WEB_FRAMEWORK_SUBSCRIBERS.unregister()
    WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
    with core.context_with_event(request_event()) as active:
        span = active.get_item("_inner_span")
        assert span is not None
        web_policy.configure(appsec_enabled=False)
        assert not web_policy.enabled
        assert not core.has_listeners("context.started.web.request")
        assert core.has_listeners("context.ended.web.request")
        with core.context_with_event(request_event()) as skipped:
            assert skipped.get_item("_inner_span") is None
    assert span.finished
    assert not core.has_listeners("context.ended.web.request")
