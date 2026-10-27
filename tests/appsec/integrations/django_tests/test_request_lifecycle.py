"""Django analysis uses the request events before tracing exports the span."""

import asyncio
from contextlib import closing
from types import ModuleType
from unittest import mock

import django
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.test import Client
from django.test import override_settings
from django.urls import path
import pytest

from ddtrace.appsec._iast import _request_lifecycle
from ddtrace.appsec._iast import processor
from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast_context import IAST_CONTEXT
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from tests.appsec.iast.iast_utils import load_iast_report
from tests.utils import override_global_config


@pytest.fixture
def request_events(iast_span, tracer):
    tracer._recreate()
    notifications = []
    free_slots = debug_context_array_free_slots_number()

    def observe(event):
        if event.ctx.get_item("integration_config").integration_name == "django":
            assert not event.span.finished
            if isinstance(event, WebRequestCompleting) and tracer.enabled:
                assert event.span.get_metric(IAST.ENABLED) in (0.0, 1.0)
            notifications.append(event)

    for event in (WebRequestReady, WebRequestCompleting):
        core.on(event.event_name, observe)
    try:
        yield notifications
        assert debug_context_array_free_slots_number() == free_slots
    finally:
        for event in (WebRequestReady, WebRequestCompleting):
            core.reset_listeners(event.event_name, observe)


def query(value):
    import sqlite3

    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(add_aspect("SELECT 1 FROM ", value))


def assert_completed(events, analyzed=True, report=True):
    assert [type(event) for event in events] == [WebRequestReady, WebRequestCompleting] * 2
    assert events[0].span is not events[2].span
    for ready, complete in zip(events[::2], events[1::2]):
        assert ready.ctx is complete.ctx and ready.span is complete.span
        assert complete.span.finished
        assert complete.span.get_metric(IAST.ENABLED) == float(analyzed)
        findings = load_iast_report(complete.span)
        if analyzed and report:
            assert [v["type"] for v in findings["vulnerabilities"]] == [VULN_SQL_INJECTION]
        else:
            assert findings is None


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["normal", "error", "propagate_error", "stream", "setup_error", "finalize_error"])
def test_wsgi_request_lifecycle(request_events, tracer, mode):
    urls = ModuleType("lifecycle_urls")

    def view(request):
        value = request.GET["table"]
        assert get_tainted_ranges(value)
        context_id = IAST_CONTEXT.get()
        with tracer.trace("manual.web", span_type="web"):
            assert IAST_CONTEXT.get() == context_id
        with tracer.trace("manual.changed") as span:
            span.span_type = "web"
        assert IAST_CONTEXT.get() == context_id
        assert get_tainted_ranges(value)
        query(value)
        if mode in ("error", "propagate_error"):
            raise RuntimeError("lifecycle fixture")
        if mode == "stream":
            return StreamingHttpResponse(iter([b"ok"]))
        return HttpResponse("ok")

    urls.urlpatterns = [path("lifecycle", view)]
    original_dispatch = core.dispatch

    def dispatch(event, *args, **kwargs):
        if (mode == "setup_error" and event == "django.traced_get_response.pre") or (
            mode == "finalize_error" and event == "django.finalize_response.pre"
        ):
            raise RuntimeError("lifecycle fixture")
        return original_dispatch(event, *args, **kwargs)

    with (
        override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[], DEBUG_PROPAGATE_EXCEPTIONS=mode == "propagate_error"),
        mock.patch.object(core, "dispatch", dispatch),
    ):
        client = Client()
        # Reusing the same thread must not leak analysis or quota slots.
        for _ in range(2):
            if mode in ("error", "propagate_error", "setup_error", "finalize_error"):
                with pytest.raises(RuntimeError, match="lifecycle fixture"):
                    client.get("/lifecycle?table=sqlite_master")
            else:
                response = client.get("/lifecycle?table=sqlite_master")
                if mode == "stream":
                    # WSGI's existing request span ends when get_response returns.
                    assert request_events[-1].span.finished
                    assert list(response.streaming_content) == [b"ok"]
                else:
                    assert response.content == b"ok"
                response.close()
            assert IAST_CONTEXT.get() is None
    assert_completed(request_events, report=mode != "setup_error")


@pytest.mark.parametrize("protocol", ["wsgi", "asgi"])
@pytest.mark.parametrize("tracing_enabled,sampling", [(True, 100), (True, 0), (False, 100)])
def test_django_admission(request_events, tracer, monkeypatch, tracing_enabled, sampling, protocol):
    if protocol == "asgi" and django.VERSION < (3, 1):
        pytest.skip("Django ASGI instrumentation requires Django 3.1+")
    from ddtrace.appsec._iast._overhead_control_engine import oce

    try:
        with override_global_config(dict(_iast_request_sampling=sampling)):
            oce.reconfigure()
            urls = ModuleType("admission_urls")

            def view(request):
                analyzed = tracing_enabled and sampling != 0
                assert (IAST_CONTEXT.get() is not None) is analyzed
                assert bool(get_tainted_ranges(request.GET["table"])) is analyzed
                return HttpResponse("ok")

            urls.urlpatterns = [path("lifecycle", view)]
            with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
                application = None
                if protocol == "asgi":
                    from django.core.asgi import get_asgi_application

                    # Build the installed middleware before disabling tracing at runtime.
                    application = get_asgi_application()
                monkeypatch.setattr(tracer, "enabled", tracing_enabled)
                for _ in range(2):
                    if protocol == "wsgi":
                        assert Client().get("/lifecycle?table=sqlite_master").status_code == 200
                    else:
                        asyncio.run(invoke_asgi(application))
                    assert IAST_CONTEXT.get() is None
            if tracing_enabled:
                assert_completed(request_events, analyzed=sampling != 0, report=False)
            else:
                assert len(request_events) == 4
                assert all(event.span.get_metric(IAST.ENABLED) is None for event in request_events)
    finally:
        # Restore the sampler after override_global_config restores its settings.
        oce.reconfigure()


async def invoke_asgi(application, disconnect=None):
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if disconnect is not None:
            await disconnect.wait()
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()

    async def send(message):
        pass

    scope = dict(
        type="http",
        asgi={"version": "3.0"},
        http_version="1.1",
        method="GET",
        scheme="http",
        path="/lifecycle",
        root_path="",
        query_string=b"table=sqlite_master",
        headers=[],
        server=("localhost", 80),
        client=("127.0.0.1", 1234),
    )
    await application(scope, receive, send)


@pytest.mark.skipif(django.VERSION < (4, 2), reason="Async streaming responses require Django 4.2+")
@pytest.mark.parametrize(
    "mode", ["normal", "error", "propagate_error", "stream", "stream_error", "cancel", "disconnect", "setup_error"]
)
def test_asgi_request_lifecycle(request_events, tracer, mode):
    from django.core.asgi import get_asgi_application

    if mode == "disconnect" and django.VERSION < (5, 0):
        pytest.skip("Django cancels disconnected request handlers on 5.0+")
    urls = ModuleType("asgi_lifecycle_urls")
    disconnected = []
    started = None

    async def view(request):
        value = request.GET["table"]
        assert get_tainted_ranges(value)
        with tracer.trace("manual.web", span_type="web"):
            pass
        assert get_tainted_ranges(value)
        if mode == "cancel":
            query(value)
            raise asyncio.CancelledError()
        if mode in ("normal", "error", "propagate_error", "disconnect"):
            query(value)
            if mode in ("error", "propagate_error"):
                raise RuntimeError("lifecycle fixture")
            if mode == "disconnect":
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    disconnected.append(True)
                    raise
            return HttpResponse("ok")

        async def chunks():
            assert get_tainted_ranges(value)
            query(value)
            if mode == "stream_error":
                raise RuntimeError("lifecycle fixture")
            yield b"ok"

        return StreamingHttpResponse(chunks())

    urls.urlpatterns = [path("lifecycle", view)]

    async def run(application):
        nonlocal started
        for _ in range(2):
            started = asyncio.Event()
            if mode == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await invoke_asgi(application)
            elif mode in ("propagate_error", "stream_error", "setup_error"):
                with pytest.raises(RuntimeError, match="lifecycle fixture"):
                    await invoke_asgi(application)
            else:
                await invoke_asgi(application, started if mode == "disconnect" else None)

    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[], DEBUG_PROPAGATE_EXCEPTIONS=mode == "propagate_error"):
        application = get_asgi_application()
        if mode == "setup_error":

            def fail_setup(span, scope):
                raise RuntimeError("lifecycle fixture")

            application.span_modifier = fail_setup
        asyncio.run(run(application))
    assert_completed(request_events, report=mode != "setup_error")
    if mode == "disconnect":
        assert disconnected == [True, True]
    if mode in ("error", "propagate_error"):
        assert all(event.span.error for event in request_events)


def test_django_uses_event_helpers(request_events):
    """Request admission/finalization must bypass the legacy callbacks."""
    with (
        mock.patch.object(processor, "_iast_start_request", wraps=processor._iast_start_request) as legacy_start,
        mock.patch.object(processor, "_iast_end_request", wraps=processor._iast_end_request) as legacy_end,
        mock.patch.object(
            _request_lifecycle, "_iast_start_request", wraps=_request_lifecycle._iast_start_request
        ) as start,
        mock.patch.object(_request_lifecycle, "_iast_end_request", wraps=_request_lifecycle._iast_end_request) as end,
    ):
        for _ in range(2):
            Client().get("/appsec/weak-hash/")
        legacy_start.assert_not_called()
        legacy_end.assert_not_called()
        assert start.call_count == end.call_count == 2


@pytest.mark.parametrize("event_type", [WebRequestReady, WebRequestCompleting])
def test_wsgi_listener_interruption(request_events, event_type):
    def interrupt(event):
        if event.ctx.get_item("integration_config").integration_name == "django":
            raise KeyboardInterrupt("lifecycle listener")

    core.on(event_type.event_name, interrupt)
    try:
        with pytest.raises(KeyboardInterrupt, match="lifecycle listener"):
            Client().get("/appsec/weak-hash/")
    finally:
        core.reset_listeners(event_type.event_name, interrupt)
    assert IAST_CONTEXT.get() is None
    assert [type(event) for event in request_events] == [WebRequestReady, WebRequestCompleting]
    assert request_events[-1].span.finished
    assert request_events[-1].span.get_metric(IAST.ENABLED) == 1.0
    assert Client().get("/appsec/weak-hash/").status_code == 200
    assert IAST_CONTEXT.get() is None
    assert len(request_events) == 4


@pytest.mark.parametrize("protocol", ["wsgi", "asgi"])
def test_nested_django_borrows_outer_analysis(iast_span, tracer, protocol):
    from ddtrace import config
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
    from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
    from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware

    if protocol == "asgi" and django.VERSION < (3, 1):
        pytest.skip("Django ASGI instrumentation requires Django 3.1+")
    tracer._recreate()
    free_slots = debug_context_array_free_slots_number()
    roots = []
    urls = ModuleType("nested_lifecycle_urls")
    urls.urlpatterns = [path("lifecycle", lambda request: HttpResponse("ok"))]

    def before_inner():
        roots.append(tracer.current_root_span())
        context_id = IAST_CONTEXT.get()
        assert context_id is not None
        value = taint_pyobject("sqlite_master", "table", "sqlite_master")
        return context_id, value

    def after_inner(context_id, value):
        assert IAST_CONTEXT.get() == context_id
        assert get_tainted_ranges(value)
        query(value)

    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
        if protocol == "wsgi":
            from wsgiref.util import setup_testing_defaults

            def outer(environ, start_response):
                context_id, value = before_inner()
                assert Client().get("/lifecycle").status_code == 200
                after_inner(context_id, value)
                start_response("200 OK", [])
                return [b"ok"]

            for _ in range(2):
                environ = {}
                setup_testing_defaults(environ)
                with closing(DDWSGIMiddleware(outer)(environ, mock.Mock())) as response:
                    assert list(response) == [b"ok"]
        else:
            from django.core.asgi import get_asgi_application

            application = get_asgi_application()

            async def outer(scope, receive, send):
                context_id, value = before_inner()

                async def defer_completion(message):
                    if message["type"] == "http.response.body":
                        message = dict(message, more_body=True)
                    await send(message)

                await application(scope, receive, defer_completion)
                after_inner(context_id, value)
                await send({"type": "http.response.body", "body": b"", "more_body": False})

            async def run():
                for _ in range(2):
                    await invoke_asgi(TraceMiddleware(outer, integration_config=config.django))

            asyncio.run(run())
    assert len(roots) == 2
    assert all(span.finished for span in roots)
    for span in roots:
        assert [v["type"] for v in load_iast_report(span)["vulnerabilities"]] == [VULN_SQL_INJECTION]
    assert debug_context_array_free_slots_number() == free_slots
