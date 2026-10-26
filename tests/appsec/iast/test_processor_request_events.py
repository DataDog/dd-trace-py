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
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.internal import span_bus
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import Span


@contextmanager
def active_context(identifier, integration):
    ctx = core.ExecutionContext(identifier, parent=core.current, integration_config=integration)
    with mock.patch.object(core, "dispatch"), ctx:
        yield ctx


@pytest.fixture
def processor(monkeypatch):
    monkeypatch.setattr(asm_config, "_iast_enabled", True)
    monkeypatch.setattr(
        core, "has_listeners", lambda event: event in (WebRequestReady.event_name, WebRequestCompleting.event_name)
    )
    return AppSecIastSpanProcessor()


@pytest.mark.parametrize(
    "identifier,integration,listening,migrated",
    [
        ("asgi.request", "fastapi", True, True),
        ("asgi.request", "fastapi", False, False),
        ("wsgi.__call__", "flask", True, False),
        ("asgi.request", "django", True, False),
        ("fastapi.serialize_response", "fastapi", True, False),
    ],
)
def test_request_route_survives_context_exit_and_processor_replacement(
    processor, monkeypatch, identifier, integration, listening, migrated
):
    monkeypatch.setattr(
        core,
        "has_listeners",
        lambda event: listening and event in (WebRequestReady.event_name, WebRequestCompleting.event_name),
    )
    with (
        mock.patch.object(module, "_iast_start_request", return_value=None) as start,
        mock.patch.object(module, "_iast_end_request") as end,
        mock.patch.object(lifecycle, "_iast_start_request") as event_start,
        mock.patch.object(lifecycle, "_iast_end_request") as event_end,
    ):
        with active_context(identifier, getattr(config, integration)) as ctx:
            span = Span("arbitrary.request.name", span_type="web")
            processor.on_span_start(span)
            span_bus.store_span_on_context(ctx, span)
            if listening:
                lifecycle.IastRequestReadySubscriber.on_event(WebRequestReady(ctx, span))
            child = Span("manual.web", span_type="web")
            processor.on_span_start(child)
            processor.on_span_finish(child)
            child.finish()
        # Completion does not depend on the current scope or mutable component tag.
        span.set_tag("component", "fastapi")
        if listening:
            lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(ctx, span))
        AppSecIastSpanProcessor().on_span_finish(span)
        expected_legacy_calls = [] if migrated else [mock.call(span)]
        assert start.call_args_list == expected_legacy_calls + [mock.call(child)]
        assert end.call_args_list == [mock.call(child)] + expected_legacy_calls
        expected_event_calls = [mock.call(span)] if migrated else []
        assert event_start.call_args_list == event_end.call_args_list == expected_event_calls
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
