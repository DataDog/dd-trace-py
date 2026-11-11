from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import core
from ddtrace.internal.core.subscriber import SubscriberBundle


def test_tracing_bundle_registration_preserves_request_completion(tracer, test_spans) -> None:
    from ddtrace._trace import product
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.span_bus import span_from_context

    core.reset_listeners("context.started.web.request")
    core.reset_listeners("context.ended.web.request")
    product.start()
    product.start()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        assert not span.finished
        event.response_status_code = 201
    assert span.finished
    assert span.get_tag("http.status_code") == "201"
    assert test_spans.pop() == [span]


@pytest.mark.subprocess
def test_tracing_subscriber_initialization_supports_manual_instrumentation() -> None:
    import sys

    import ddtrace._trace.subscribers  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.internal.products import manager

    assert manager._products is None
    assert "ddtrace._trace.product" not in sys.modules
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_tracing_product_declaration_does_not_import_subscribers() -> None:
    import sys

    from ddtrace._trace import product
    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS

    assert product.subscriber_bundles[0].module == "ddtrace._trace.subscribers.web_framework"
    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
    assert product.subscriber_bundles == (WEB_FRAMEWORK_SUBSCRIBERS,)


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_subscriber_initialization_after_product_stop_stays_inactive() -> None:
    import sys

    from ddtrace._trace import product
    from ddtrace.internal import core

    assert "ddtrace._trace.subscribers" not in sys.modules
    product.stop()
    assert "ddtrace._trace.subscribers" not in sys.modules

    import ddtrace._trace.subscribers  # noqa: F401

    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")


@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_tracing_product_registers_bundles_and_unregisters_after_shutdown(tracing_enabled, monkeypatch):
    from ddtrace._trace import product

    bundle = Mock(spec=SubscriberBundle)
    monkeypatch.setattr(product, "subscriber_bundles", (bundle,))
    with patch("ddtrace.trace.tracer") as tracer:
        tracer.enabled = tracing_enabled

        def shutdown():
            bundle.unregister.assert_not_called()
            tracer.start_span = tracer._start_span_after_shutdown

        tracer.shutdown.side_effect = shutdown
        product.start()
        bundle.register.assert_called_once_with()
        product.stop()
        tracer.shutdown.assert_called_once_with()
        bundle.unregister.assert_called_once_with()


def test_tracing_product_retains_listeners_when_shutdown_fails(monkeypatch):
    from ddtrace._trace import product

    bundle = Mock(spec=SubscriberBundle)
    monkeypatch.setattr(product, "subscriber_bundles", (bundle,))
    with patch("ddtrace.trace.tracer") as tracer:
        tracer.enabled = True
        tracer.shutdown.side_effect = RuntimeError("shutdown failed")
        with pytest.raises(RuntimeError, match="shutdown failed"):
            product.stop()
        bundle.unregister.assert_not_called()
        core.dispatch("tracer.shutdown", (tracer,), allow_raise=True)
        bundle.unregister.assert_called_once_with()
        assert not core.has_listeners("tracer.shutdown")


@pytest.mark.subprocess(
    env=dict(DD_TRACE_ENABLED="true", DD_APM_TRACING_ENABLED="true"),
    parametrize={
        "DISABLE_BEFORE_STOP": ["true", "false"],
        "STOP_IN_OTHER_TASK": ["true", "false"],
        "DEFER_COMPLETION": ["true", "false"],
    },
    err=lambda err: err.startswith("Shutting down tracer with 1 spans.") and len(err.splitlines()) == 1,
)
def test_tracing_product_stop_discards_active_requests_and_unregisters() -> None:
    import asyncio
    from dataclasses import replace
    import os
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.products import ProductManager
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.trace import tracer

    manager = ProductManager()
    manager._products = [("apm-tracing", product)]
    manager.start_products()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    other_task = os.environ["STOP_IN_OTHER_TASK"] == "true"
    deferred = os.environ["DEFER_COMPLETION"] == "true"

    async def stop():
        manager.stop_products()
        assert not tracer.enabled
        assert not tracer._span_aggregator._traces
        assert tracer.context_provider.active() is None
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")

    async def request():
        with core.context_with_event(event, dispatch_end_event=not deferred) as ctx:
            span = ctx.get_item("_inner_span")
            assert span is not None
            assert not span.finished
            assert span.trace_id in tracer._span_aggregator._traces
            if os.environ["DISABLE_BEFORE_STOP"] == "true":
                tracer.enabled = False
            if other_task:
                await asyncio.create_task(stop())
                # Shutdown clears only its caller's ContextVar, not an existing request task's copy.
                assert tracer.current_span() is span
            else:
                await stop()
                assert tracer.current_span() is None

            with core.context_with_event(replace(event)) as new_ctx:
                assert new_ctx.get_item("_inner_span") is None
            event.response_status_code = 201

        if deferred:
            ctx.dispatch_ended_event()
        assert ctx._event is None
        assert not span.finished
        assert span.get_tag("http.status_code") is None
        assert not tracer._span_aggregator._traces
        # A late manual finish cannot resurrect the discarded trace either.
        span.finish()
        assert not tracer._span_aggregator._traces
        assert tracer.context_provider.active() is None

    with patch.object(tracer._span_aggregator.writer, "write") as write:
        asyncio.run(request())
        write.assert_not_called()


@pytest.mark.subprocess
def test_tracing_product_stop_after_public_shutdown_does_not_repeat_cleanup() -> None:
    from unittest.mock import Mock
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.internal import core
    from ddtrace.internal.span_processor import SpanProcessor
    from ddtrace.trace import tracer

    product.start()
    processor = Mock(spec=SpanProcessor)
    processor.shutdown.side_effect = [None, RuntimeError("cleanup repeated")]
    with patch.object(SpanProcessor, "__processors__", [processor]):
        tracer.shutdown()
        product.stop()
    processor.shutdown.assert_called_once_with(None)
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess
def test_tracing_product_stop_does_not_repeat_concurrently_completed_shutdown() -> None:
    import threading
    from unittest.mock import Mock
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.internal import core
    from ddtrace.internal.span_processor import SpanProcessor
    from ddtrace.trace import tracer

    product.start()
    processor = Mock(spec=SpanProcessor)
    processor.shutdown.side_effect = [None, RuntimeError("cleanup repeated")]
    shutdown = tracer.shutdown
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def delayed_shutdown(timeout=None):
        entered.set()
        assert release.wait(10)
        shutdown(timeout)

    def stop():
        try:
            product.stop()
        except BaseException as error:
            errors.append(error)

    with (
        patch.object(SpanProcessor, "__processors__", [processor]),
        patch.object(tracer, "shutdown", side_effect=delayed_shutdown),
    ):
        thread = threading.Thread(target=stop)
        thread.start()
        try:
            assert entered.wait(10)
            # Complete public shutdown after stop entered its shutdown call but before it acquires the lock.
            shutdown()
        finally:
            release.set()
            thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
    processor.shutdown.assert_called_once_with(None)
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess
def test_tracing_product_stop_retries_incomplete_shutdown() -> None:
    from unittest.mock import Mock
    from unittest.mock import patch

    import pytest

    from ddtrace._trace import product
    from ddtrace.internal import core
    from ddtrace.internal.span_processor import SpanProcessor
    from ddtrace.trace import tracer

    product.start()
    processor = Mock(spec=SpanProcessor)
    processor.shutdown.side_effect = [RuntimeError("cleanup failed"), None]
    with patch.object(SpanProcessor, "__processors__", [processor]):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            tracer.shutdown()
        assert tracer._shutting_down
        assert core.has_listeners("context.ended.web.request")
        product.stop()
    assert processor.shutdown.call_count == 2
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess(parametrize={"SPAN_PROCESSING_MODE": ["apm", "standalone", "llmobs"]})
def test_tracing_product_stop_does_not_buffer_requests_during_writer_shutdown() -> None:
    import os
    import threading
    from unittest.mock import Mock
    from unittest.mock import PropertyMock
    from unittest.mock import patch

    from ddtrace import config
    from ddtrace._trace import product
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.settings.standalone import standalone_config
    from ddtrace.internal.span_processor import SpanProcessor
    from ddtrace.trace import tracer

    product.start()
    mode = os.environ["SPAN_PROCESSING_MODE"]
    tracer.enabled = mode == "apm"
    config._llmobs_enabled = mode == "llmobs"
    entered = threading.Event()
    release = threading.Event()
    errors = []
    processor = Mock(spec=SpanProcessor)
    writer = tracer._span_aggregator.writer
    original_stop = writer.stop

    def paused_stop(timeout=None):
        entered.set()
        assert release.wait(10)
        original_stop(timeout)

    def stop():
        try:
            product.stop()
        except BaseException as error:
            errors.append(error)

    with (
        patch.object(SpanProcessor, "__processors__", [processor]),
        patch.object(writer, "stop", side_effect=paused_stop),
        patch.object(
            type(standalone_config), "apm_opt_out", new_callable=PropertyMock, return_value=mode == "standalone"
        ),
    ):
        thread = threading.Thread(target=stop)
        thread.start()
        try:
            assert entered.wait(10)
            tracer.start_span("during.shutdown").finish()
            late_span = tracer.start_span("finishes.after.shutdown")
            # Completion handlers stay installed until terminal cleanup succeeds.
            assert core.has_listeners("context.ended.web.request")
            with core.context_with_event(
                WebFrameworkRequestEvent(
                    http_operation="test.request",
                    component="test",
                    integration_config=IntegrationConfig(config, "test"),
                    request_url="http://localhost/",
                    request_method="GET",
                    request_headers={},
                    headers_case_sensitive=True,
                ),
                dispatch_end_event=False,
            ) as ctx:
                pass
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive()
        assert not errors, errors
        assert not tracer._span_aggregator._traces
        late_span.finish()
        ctx.dispatch_ended_event()
        processor.shutdown.assert_called_once_with(None)
        processor.on_span_start.assert_not_called()
        processor.on_span_finish.assert_not_called()
        assert not tracer._span_aggregator._traces
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess(parametrize={"SHUTDOWN_FAILS": ["false", "true"]})
def test_tracing_product_unregisters_after_concurrent_public_shutdown() -> None:
    import os
    import threading
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.internal import core
    from ddtrace.internal.products import ProductManager
    from ddtrace.trace import tracer

    manager = ProductManager()
    manager._products = [("apm-tracing", product)]
    manager.start_products()
    fail = os.environ["SHUTDOWN_FAILS"] == "true"
    entered = threading.Event()
    release = threading.Event()
    errors = []
    writer = tracer._span_aggregator.writer
    original_stop = writer.stop

    def paused_stop(timeout=None):
        entered.set()
        assert release.wait(10)
        if fail:
            raise RuntimeError("shutdown failed")
        original_stop(timeout)

    def shutdown():
        try:
            tracer.shutdown()
        except BaseException as error:
            errors.append(error)

    with patch.object(writer, "stop", side_effect=paused_stop):
        thread = threading.Thread(target=shutdown)
        thread.start()
        try:
            assert entered.wait(10)
            manager.stop_products()
            core.dispatch("tracer.shutdown", (object(),), allow_raise=True)
            assert core.has_listeners("context.ended.web.request")
        finally:
            release.set()
            thread.join(10)
    assert not thread.is_alive()
    if fail:
        assert len(errors) == 1 and str(errors[0]) == "shutdown failed"
        assert core.has_listeners("context.ended.web.request")
        tracer.shutdown()
    else:
        assert not errors, errors
    # ProductManager stops each product once; public shutdown must complete its teardown.
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")

    assert not core.has_listeners("tracer.shutdown")


@pytest.mark.subprocess
def test_tracing_product_unregisters_after_reentrant_stop() -> None:
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.internal import core
    from ddtrace.internal.products import ProductManager
    from ddtrace.trace import tracer

    manager = ProductManager()
    manager._products = [("apm-tracing", product)]
    manager.start_products()
    writer = tracer._span_aggregator.writer
    original_stop = writer.stop

    def stop_product(timeout=None):
        manager.stop_products()
        assert core.has_listeners("context.ended.web.request")
        original_stop(timeout)

    with patch.object(writer, "stop", side_effect=stop_product):
        tracer.shutdown()
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    assert not core.has_listeners("tracer.shutdown")


@pytest.mark.subprocess(
    parametrize={"CALLBACK": ["start", "finish"]},
    err=lambda err: not err or (err.startswith("Shutting down tracer with 1 spans.") and len(err.splitlines()) == 1),
)
def test_tracing_shutdown_detaches_buffer_from_inflight_callbacks() -> None:
    from collections import defaultdict
    import os
    import threading
    from unittest.mock import Mock
    from unittest.mock import patch

    from ddtrace._trace.processor import _Trace
    from ddtrace.trace import Span
    from ddtrace.trace import tracer

    aggregator = tracer._span_aggregator
    entered = threading.Event()
    release = threading.Event()
    errors = []
    span = Span("test.request")
    callback = os.environ["CALLBACK"]

    class PausedTraces(defaultdict[int, _Trace]):
        def __getitem__(self, key: int) -> _Trace:
            entered.set()
            assert release.wait(10)
            return self.setdefault(key, _Trace())

    retired = PausedTraces(_Trace)
    if callback == "finish":
        retired[span.trace_id] = _Trace([span])
        span.finish()
    aggregator._traces = retired

    def handle():
        try:
            if callback == "start":
                aggregator.on_span_start(span)
            else:
                aggregator.on_span_finish(span)
        except BaseException as error:
            errors.append(error)

    writer = Mock()
    with patch.object(aggregator, "writer", writer):
        thread = threading.Thread(target=handle)
        thread.start()
        try:
            assert entered.wait(10)
            # The callback owns the aggregation lock. Shutdown must not wait for it.
            aggregator.shutdown(0)
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive()
        assert not errors, errors
        assert not aggregator._traces
        aggregator.on_span_start(Span("after.shutdown"))
        assert not aggregator._traces
