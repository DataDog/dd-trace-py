"""Temporary coexistence coverage; remove with AppSecIastSpanProcessor.

Request reporting and cleanup are covered by the shared helper and IAST integration
suites. Keep only routing and exclusion bookkeeping checks here.
"""

from contextlib import contextmanager
import gc
from unittest import mock
import weakref

import pytest

from ddtrace import config
from ddtrace.appsec._iast import _request_lifecycle as lifecycle
from ddtrace.appsec._iast import processor as module
from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import Span


@contextmanager
def active_context(identifier, integration, protocol="http"):
    ctx = core.ExecutionContext(
        identifier, parent=core.current, integration_config=integration, scope={"type": protocol}
    )
    with mock.patch.object(core, "dispatch"), ctx:
        yield ctx


@pytest.fixture
def processor(monkeypatch):
    monkeypatch.setattr(asm_config, "_iast_enabled", True)
    monkeypatch.setattr(
        core, "has_listeners", lambda event: event in ("span.started.asgi.request", "span.finishing.asgi.request")
    )
    return AppSecIastSpanProcessor()


@pytest.mark.parametrize(
    "identifier,integration,protocol,listening,migrated",
    [
        ("asgi.request", "fastapi", "http", True, True),
        ("asgi.request", "fastapi", "http", False, False),
        ("asgi.request", "fastapi", "websocket", True, False),
        ("wsgi.__call__", "flask", "http", True, False),
        ("asgi.request", "django", "http", True, False),
        ("fastapi.serialize_response", "fastapi", "http", True, False),
    ],
)
def test_request_route_survives_context_exit_and_processor_replacement(
    processor, monkeypatch, identifier, integration, protocol, listening, migrated
):
    monkeypatch.setattr(
        core,
        "has_listeners",
        lambda event: listening and event in ("span.started.asgi.request", "span.finishing.asgi.request"),
    )
    with (
        mock.patch.object(module, "_iast_start_request", return_value=None) as start,
        mock.patch.object(module, "_iast_end_request") as end,
        mock.patch.object(lifecycle, "_iast_start_request") as event_start,
        mock.patch.object(lifecycle, "_iast_end_request") as event_end,
    ):
        with active_context(identifier, getattr(config, integration), protocol) as ctx:
            span = Span("arbitrary.request.name", span_type="web")
            processor.on_span_start(span)
            span_bus.store_span_on_context(ctx, span)
            if listening:
                lifecycle.IastRequestStartedSubscriber.on_event(ctx)
            child = Span("manual.web", span_type="web")
            processor.on_span_start(child)
            processor.on_span_finish(child)
            child.finish()
        # Completion does not depend on the current scope or mutable component tag.
        span.set_tag("component", "fastapi")
        if listening:
            lifecycle.IastRequestFinishingSubscriber.on_event(ctx)
        AppSecIastSpanProcessor().on_span_finish(span)
        expected_legacy_calls = [] if migrated else [mock.call(span)]
        assert start.call_args_list == expected_legacy_calls + [mock.call(child)]
        assert end.call_args_list == [mock.call(child)] + expected_legacy_calls
        assert event_start.call_args_list == ([mock.call(span)] if migrated else [])
        assert event_end.call_args_list == ([mock.call(span, context=ctx)] if migrated else [])
        span.finish()


def test_manual_span_becoming_web_keeps_legacy_finish(processor):
    with (
        mock.patch.object(module, "_iast_start_request") as start,
        mock.patch.object(module, "_iast_end_request") as end,
    ):
        span = Span("manual")
        processor.on_span_start(span)
        span.span_type = "web"
        processor.on_span_finish(span)
        start.assert_not_called()
        end.assert_called_once_with(span)
        span.finish()


def test_processor_does_not_retain_unfinished_spans(processor):
    with active_context("asgi.request", config.fastapi):
        span = Span("manual", span_type="web")
        processor.on_span_start(span)
    reference = weakref.ref(span)
    del span
    gc.collect()
    assert reference() is None


def test_early_finish_completes_once_before_late_notification(processor):
    with (
        mock.patch.object(module, "_iast_end_request") as end,
        mock.patch.object(lifecycle, "_iast_start_request"),
        mock.patch.object(lifecycle, "_iast_end_request") as event_end,
    ):
        with active_context("asgi.request", config.fastapi) as ctx:
            span = Span("request", span_type="web")
            processor.on_span_start(span)
            span_bus.store_span_on_context(ctx, span)
            lifecycle.IastRequestStartedSubscriber.on_event(ctx)
            processor.on_span_finish(span)
        # A replacement processor and late notification must not complete again.
        AppSecIastSpanProcessor().on_span_finish(span)
        lifecycle.IastRequestFinishingSubscriber.on_event(ctx)
        end.assert_called_once_with(span)
        event_end.assert_not_called()
        span.finish()


def test_subapp_without_notifications_keeps_legacy_route(processor):
    with (
        mock.patch.object(module, "_iast_start_request") as start,
        mock.patch.object(module, "_iast_end_request") as end,
        active_context("asgi.request", config.fastapi) as ctx,
    ):
        ctx.set_item("is_subapp", True)
        span = Span("request", span_type="web")
        processor.on_span_start(span)
        span_bus.store_span_on_context(ctx, span)
        processor.on_span_finish(span)
        start.assert_called_once_with(span)
        end.assert_called_once_with(span)
        span.finish()
