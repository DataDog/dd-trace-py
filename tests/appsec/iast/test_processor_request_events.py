"""Temporary coexistence coverage; remove with AppSecIastSpanProcessor.

Request reporting and cleanup are covered by test_request_lifecycle and the IAST
integration suites. Keep only routing and exclusion bookkeeping checks here.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import gc
from unittest import mock
import weakref

import pytest

from ddtrace import config
from ddtrace.appsec._iast import _request_lifecycle as lifecycle
from ddtrace.appsec._iast import processor as module
from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import Span
from tests.utils import override_global_config


@contextmanager
def active_context(identifier, integration):
    ctx = core.ExecutionContext(identifier, parent=core.current, integration_config=integration)
    with core.activate_context(ctx):
        yield ctx


@pytest.fixture
def processor(monkeypatch):
    monkeypatch.setattr(asm_config, "_iast_enabled", True)
    monkeypatch.setattr(core, "has_listeners", lambda _event: True)
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
    monkeypatch.setattr(core, "has_listeners", lambda _event: listening)
    with (
        mock.patch.object(module, "_iast_start_request", return_value=None) as start,
        mock.patch.object(module, "_iast_end_request") as end,
        mock.patch.object(lifecycle, "_iast_start_request") as event_start,
    ):
        with active_context(identifier, getattr(config, integration)) as ctx:
            span = Span("arbitrary.request.name", span_type="web")
            processor.on_span_start(span)
            if listening:
                lifecycle.IastRequestReadySubscriber.on_event(WebRequestReady(ctx, span))
        # Completion does not depend on the current scope or mutable component tag.
        span.set_tag("component", "fastapi")
        AppSecIastSpanProcessor().on_span_finish(span)
        expected_legacy_calls = [] if migrated else [mock.call(span)]
        assert start.call_args_list == expected_legacy_calls
        assert end.call_args_list == expected_legacy_calls
        assert event_start.call_args_list == ([mock.call(span)] if migrated else [])
        span.finish()


@pytest.mark.parametrize("completed", [False, True])
def test_typed_legacy_request_keeps_admission_after_fastapi(processor, monkeypatch, completed):
    monkeypatch.setattr(lifecycle, "IAST_CONTEXT", ContextVar("test_iast_context", default=None))
    event = WebFrameworkRequestEvent(
        component="bottle",
        integration_config=config.bottle,
        http_operation="bottle.request",
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    with (
        active_context("asgi.request", config.fastapi) as outer,
        mock.patch.object(module, "_iast_start_request") as start,
        mock.patch.object(module, "_iast_end_request") as end,
    ):
        outer.set_item(
            lifecycle._REQUEST_STATE,
            lifecycle._RequestState(Span("outer"), 0 if completed else None, completed=completed),
        )
        ctx = core.ExecutionContext(event.event_name, parent=outer, event=event)
        with core.activate_context(ctx), Span("legacy.request", span_type="web") as span:
            processor.on_span_start(span)
            processor.on_span_finish(span)
        start.assert_called_once_with(span)
        end.assert_called_once_with(span)


@pytest.mark.parametrize("capacity_rejected", [False, True])
@pytest.mark.parametrize("is_subapp", [False, True])
def test_enclosing_legacy_request_keeps_finish_callback(processor, capacity_rejected, is_subapp):
    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100)):
        initialize_native_state()
        oce.reconfigure()
        baseline = debug_context_array_free_slots_number()
        busy = [start_request_context() for _ in range(debug_context_array_size())] if capacity_rejected else []
        try:
            with (
                mock.patch.object(module, "_iast_end_request", wraps=module._iast_end_request) as end,
                mock.patch.object(lifecycle, "_iast_start_request", wraps=lifecycle._iast_start_request) as event_start,
                active_context("asgi.request", config.django),
            ):
                parent = Span("legacy.request", span_type="web")
                processor.on_span_start(parent)
                if capacity_rejected:
                    # Capacity can become available while outer middleware awaits.
                    finish_request_context(busy[0])
                with active_context("asgi.request", config.fastapi) as inner:
                    inner.set_item("is_subapp", is_subapp)
                    span = Span("fastapi.request", span_type="web")
                    span._parent = parent
                    processor.on_span_start(span)
                    lifecycle.IastRequestReadySubscriber.on_event(WebRequestReady(inner, span))
                    if capacity_rejected and not is_subapp:
                        event_start.assert_called_once_with(span)
                    else:
                        event_start.assert_not_called()
                    if not is_subapp:
                        # An independent request delivers its own response first.
                        lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(inner, span))
                    # Nested ASGI send wrappers finish the outer span first.
                    processor.on_span_finish(parent)
                    end.assert_called_once_with(parent)
                    processor.on_span_finish(span)
                    lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(inner, span))
                    assert span.get_metric(IAST.ENABLED) == (1.0 if capacity_rejected and not is_subapp else None)
                    assert not (span.get_tag(IAST.JSON) or span._get_struct_tag(IAST.STRUCT))
                    span.finish()
                assert parent.get_metric(IAST.ENABLED) == float(not capacity_rejected)
                parent.finish()
        finally:
            for context_id in busy:
                finish_request_context(context_id)
        assert debug_context_array_free_slots_number() == baseline


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
    assert not lifecycle._SPAN_ROUTES
