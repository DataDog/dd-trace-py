"""Shared IAST lifecycle contract, independent of any integration or span processor.

Keep admission, ownership and cleanup edge cases here instead of repeating them
for each migrated integration. Integration tests cover their event boundaries.
"""

import asyncio
import contextvars
import gc
from unittest import mock
import weakref

import pytest

from ddtrace.appsec._iast import _iast_request_context as request_context
from ddtrace.appsec._iast import _iast_request_context_base as request_context_base
from ddtrace.appsec._iast import _request_lifecycle as lifecycle
from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._listener import iast_listen
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import get_tainted_object_map_by_ctx_id
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.taint_sinks.sql_injection import _on_report_sqli
from ddtrace.appsec._iast_context import IAST_CONTEXT
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import Span
from tests.utils import override_global_config


def live_maps():
    return debug_context_array_size() - debug_context_array_free_slots_number()


@pytest.fixture
def request_lifecycle(monkeypatch):
    # Routing is covered by the temporary coexistence suite; these tests own their scope.
    monkeypatch.setattr(lifecycle, "_is_event_managed_request", lambda _ctx: True)
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100)
    ):
        initialize_native_state()
        oce.reconfigure()
        iast_listen()
        baseline = live_maps()
        try:
            yield
            assert live_maps() == baseline
            context_id = IAST_CONTEXT.get()
            assert context_id is None or not get_tainted_object_map_by_ctx_id(context_id)
        finally:
            _iast_finish_request()
            core.discard_item(IAST.REQUEST_CONTEXT_KEY)


def finding():
    def execute():
        pass

    query = taint_pyobject("SELECT 'unsafe'", "query", "unsafe")
    assert _on_report_sqli((query,), {}, "sqlite", execute)


def test_request_reports_before_finish_and_completes_once(request_lifecycle):
    with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
        core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
        environment = weakref.ref(_get_iast_env())
        finding()
        core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
        report = span.get_tag(IAST.JSON) or span._get_struct_tag(IAST.STRUCT)
        assert report
        core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
        assert (span.get_tag(IAST.JSON) or span._get_struct_tag(IAST.STRUCT)) == report
        assert not span.finished
        assert _get_iast_env() is None
        # Keep ownership through middleware teardown, including late child spans.
        assert lifecycle.find_request_owner(ctx) is ctx
    assert lifecycle.find_request_owner(core.current) is None
    gc.collect()
    assert environment() is None


@pytest.mark.parametrize("admitted", [False, True])
def test_nested_wrappers_reuse_admission(request_lifecycle, admitted):
    with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
        with mock.patch.object(oce, "acquire_request", return_value=admitted) as acquire:
            core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
            core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
            context_id = IAST_CONTEXT.get()
            with core.context_with_data("test.nested") as nested, Span("nested", span_type="web") as inner:
                core.dispatch_event(WebRequestReady(nested, inner), allow_raise=True)
                core.dispatch_event(WebRequestCompleting(nested, inner), allow_raise=True)
            assert IAST_CONTEXT.get() == context_id
            assert acquire.call_count == 1
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
            with core.context_with_data("test.late") as late, Span("late", span_type="web") as late_span:
                core.dispatch_event(WebRequestReady(late, late_span), allow_raise=True)
                core.dispatch_event(WebRequestCompleting(late, late_span), allow_raise=True)
            assert acquire.call_count == 1
            assert _get_iast_env() is None
        assert span.get_metric(IAST.ENABLED) == float(admitted)


def test_legacy_outer_request_keeps_ownership(request_lifecycle):
    with core.context_with_data("test.legacy"), Span("legacy", span_type="web") as parent:
        _iast_start_request(parent)
        context_id = IAST_CONTEXT.get()
        environment = _get_iast_env()
        with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as child:
            with mock.patch.object(oce, "acquire_request", side_effect=RuntimeError("must not acquire")):
                core.dispatch_event(WebRequestReady(ctx, child), allow_raise=True)
            finding()
            core.dispatch_event(WebRequestCompleting(ctx, child), allow_raise=True)
            assert IAST_CONTEXT.get() == context_id
            assert _get_iast_env() is environment
            assert child.get_metric(IAST.ENABLED) is None
            assert not (child.get_tag(IAST.JSON) or child._get_struct_tag(IAST.STRUCT))
        _iast_end_request(parent)
        assert parent.get_metric(IAST.ENABLED) == 1.0
        assert parent.get_tag(IAST.JSON) or parent._get_struct_tag(IAST.STRUCT)


def test_disabled_product_does_not_admit(request_lifecycle, monkeypatch):
    monkeypatch.setattr(asm_config, "_iast_enabled", False)
    with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
        with mock.patch.object(lifecycle, "_iast_start_request") as start:
            core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
            start.assert_not_called()
        assert not lifecycle.find_request_owner(ctx)


@pytest.mark.parametrize("admitted", [False, True])
def test_shared_environment_does_not_belong_to_an_independent_execution(request_lifecycle, admitted):
    with core.context_with_data("test.shared"), Span("other.request", span_type="web") as parent:
        # Context data is shared; ContextVar values were copied before admission.
        independent = contextvars.copy_context()
        _iast_start_request(parent)
        environment = _get_iast_env()
        context_id = IAST_CONTEXT.get()

        environment.vulnerability_budget = 7
        environment.is_first_vulnerability = False
        environment.iast_hash_object_tracking[42] = "other-request"

        def run():
            with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
                with mock.patch.object(oce, "acquire_request", return_value=admitted):
                    core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
                assert _get_iast_env() is not environment
                core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
                assert _get_iast_env() is None
                assert span.get_metric(IAST.ENABLED) == float(admitted)

        independent.run(run)
        assert IAST_CONTEXT.get() == context_id
        assert get_tainted_object_map_by_ctx_id(context_id)
        assert _get_iast_env() is environment
        assert environment.vulnerability_budget == 7
        assert environment.is_first_vulnerability is False
        assert environment.iast_hash_object_tracking == {42: "other-request"}
        _iast_end_request(parent)


@pytest.mark.parametrize("failure", ["admission", "reporting", "cancellation", "disabled_after_admission"])
def test_request_failure_always_releases_state(request_lifecycle, monkeypatch, failure):
    with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
        if failure == "admission":
            with mock.patch.object(request_context_base, "IASTEnvironment", side_effect=RuntimeError("admit fixture")):
                with pytest.raises(RuntimeError, match="admit fixture"):
                    core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
        else:
            core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
        if failure == "reporting":
            with mock.patch.object(
                request_context, "_create_and_attach_iast_report_to_span", side_effect=RuntimeError("report fixture")
            ):
                core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
        elif failure == "cancellation":
            with mock.patch.object(lifecycle, "_iast_end_request", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
        elif failure == "disabled_after_admission":
            monkeypatch.setattr(asm_config, "_iast_enabled", False)
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
        assert _get_iast_env() is None
        assert IAST_CONTEXT.get() is None
        core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)


def test_child_completion_uses_saved_request_identifier(request_lifecycle):
    async def run():
        async def complete():
            assert IAST_CONTEXT.get() is None
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)

        # Workers may exist before admission and never inherit its identifier.
        task = asyncio.create_task(complete())
        with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
            core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
            context_id = IAST_CONTEXT.get()

            await task
            assert IAST_CONTEXT.get() == context_id
            assert not get_tainted_object_map_by_ctx_id(context_id)
            assert live_maps() == 0

    asyncio.run(run())


def test_deferred_completion_restores_callback_request(request_lifecycle):
    callback_context = contextvars.copy_context()
    with core.context_with_data("test.request") as ctx:
        span = Span("request", span_type="web")
        core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
        finding()

    def complete():
        with core.context_with_data("test.callback") as callback, Span("other", span_type="web") as other:
            core.dispatch_event(WebRequestReady(callback, other), allow_raise=True)
            context_id = IAST_CONTEXT.get()
            assert context_id is not None
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
            assert core.current is callback
            assert IAST_CONTEXT.get() == context_id
            assert _get_iast_env().span is other
            assert live_maps() == 1
            core.dispatch_event(WebRequestCompleting(callback, other), allow_raise=True)

    callback_context.run(complete)
    assert span.get_tag(IAST.JSON) or span._get_struct_tag(IAST.STRUCT)
    span.finish()


@pytest.mark.parametrize("inherited_state", ["identifier", "environment", "both"])
@pytest.mark.parametrize("admitted", [False, True])
def test_ready_recovers_from_inherited_expired_request(request_lifecycle, inherited_state, admitted):
    with core.context_with_data("test.expired"), Span("expired", span_type="web") as parent:
        expired = _iast_start_request(parent)
        finish_request_context(expired)
        if inherited_state == "identifier":
            core.discard_item(IAST.REQUEST_CONTEXT_KEY)
        elif inherited_state == "environment":
            IAST_CONTEXT.set(None)
        with core.context_with_data("test.request") as ctx, Span("request", span_type="web") as span:
            with mock.patch.object(oce, "acquire_request", return_value=admitted):
                core.dispatch_event(WebRequestReady(ctx, span), allow_raise=True)
            if admitted:
                assert IAST_CONTEXT.get() not in (None, expired)
                assert _get_iast_env().span is span
            else:
                assert IAST_CONTEXT.get() is None
                assert _get_iast_env() is None
            core.dispatch_event(WebRequestCompleting(ctx, span), allow_raise=True)
            assert span.get_metric(IAST.ENABLED) == float(admitted)
