from functools import partial
from pathlib import Path
import typing as t

import ddtrace
from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry
from ddtrace.debugging._session import Session
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from tests.debugging.mocking import MockSignalUploader
from tests.utils import TracerTestCase


class MockSpanCodeOriginProcessorEntry(SpanCodeOriginProcessorEntry):
    __uploader__ = MockSignalUploader

    @classmethod
    def enable(cls):
        super().enable()

        @partial(core.on, "service_entrypoint.patch")
        def _(f: t.Callable) -> None:
            cls.instrument_view(f)

    @classmethod
    def get_uploader(cls) -> MockSignalUploader:
        return t.cast(MockSignalUploader, cls.__uploader__._instance)


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        MockSpanCodeOriginProcessorEntry.enable()

        if (uploader := MockSpanCodeOriginProcessorEntry.get_uploader()) is not None:
            uploader.flush()

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

        MockSpanCodeOriginProcessorEntry.disable()
        core.reset_listeners(event_id="service_entrypoint.patch")

    def test_span_origin(self):
        def entry_call():
            pass

        core.dispatch("service_entrypoint.patch", (entry_call,))

        with self.tracer.trace("entry"):
            entry_call()
            with self.tracer.trace("middle"):
                with self.tracer.trace("exit", span_type=SpanTypes.HTTP):
                    pass

        self.assert_span_count(3)
        entry, middle, _exit = self.get_spans()

        # Check for the expected tags on the entry span
        assert entry.get_tag("_dd.code_origin.type") == "entry"
        assert entry.get_tag("_dd.code_origin.frames.0.file") == str(Path(__file__).resolve())
        assert entry.get_tag("_dd.code_origin.frames.0.line") == str(entry_call.__code__.co_firstlineno)
        assert entry.get_tag("_dd.code_origin.frames.0.type") == __name__
        assert (
            entry.get_tag("_dd.code_origin.frames.0.method") == "SpanProbeTestCase.test_span_origin.<locals>.entry_call"
        )

        # Check that we don't have span location tags on the middle span
        assert middle.get_tag("_dd.code_origin.frames.0.file") is None
        assert middle.get_tag("_dd.code_origin.frames.0.file") is None

        # Check that we also don't have the span location tags on the exit span
        assert _exit.get_tag("_dd.code_origin.type") is None
        assert _exit.get_tag("_dd.code_origin.frames.0.file") is None
        assert _exit.get_tag("_dd.code_origin.frames.0.line") is None

    def test_span_origin_session(self):
        def entry_call():
            pass

        core.dispatch("service_entrypoint.patch", (entry_call,))

        with self.tracer.trace("entry"):
            # Emulate a trigger probe
            Session(ident="test", level=2).link_to_trace()
            entry_call()
            with self.tracer.trace("middle"):
                with self.tracer.trace("exit", span_type=SpanTypes.HTTP):
                    pass

        self.assert_span_count(3)
        entry, middle, _exit = self.get_spans()

        snapshot_ids_from_span_tags = {
            s.get_tag(f"_dd.code_origin.frames.{_}.snapshot_id") for s in (entry, middle, _exit) for _ in range(8)
        } - {None}

        payloads = MockSpanCodeOriginProcessorEntry.get_uploader().wait_for_payloads(len(snapshot_ids_from_span_tags))
        snapshot_ids = {p["debugger"]["snapshot"]["id"] for p in payloads}

        assert len(payloads) == len(snapshot_ids)

        entry_snapshot_id = entry.get_tag("_dd.code_origin.frames.0.snapshot_id")
        assert entry.get_tag("_dd.code_origin.type") == "entry"
        assert entry_snapshot_id in snapshot_ids

        # Check that we don't have span location tags on the middle span
        assert middle.get_tag("_dd.code_origin.frames.0.snapshot_id") is None

        # Check that we don't have span location tags on the exit span
        assert _exit.get_tag("_dd.code_origin.type") is None
        assert _exit.get_tag("_dd.code_origin.frames.0.snapshot_id") is None

        assert snapshot_ids_from_span_tags == snapshot_ids

    def test_span_origin_entry(self):
        def entry_call():
            pass

        core.dispatch("service_entrypoint.patch", (entry_call,))

        with self.tracer.trace("entry"):
            entry_call()
            with self.tracer.trace("middle"):
                with self.tracer.trace("exit", span_type=SpanTypes.HTTP):
                    pass

        self.assert_span_count(3)
        entry, middle, _exit = self.get_spans()

        # Check for the expected tags on the entry span
        assert entry.get_tag("_dd.code_origin.type") == "entry"
        assert entry.get_tag("_dd.code_origin.frames.0.file") == str(Path(__file__).resolve())
        assert entry.get_tag("_dd.code_origin.frames.0.line") == str(entry_call.__code__.co_firstlineno)
        assert entry.get_tag("_dd.code_origin.frames.0.type") == __name__
        assert (
            entry.get_tag("_dd.code_origin.frames.0.method")
            == "SpanProbeTestCase.test_span_origin_entry.<locals>.entry_call"
        )

        # Check that we don't have span location tags on the middle span
        assert middle.get_tag("_dd.code_origin.frames.0.file") is None
        assert middle.get_tag("_dd.code_origin.frames.0.file") is None

        # Check that we also don't have the span location tags on the exit span
        assert _exit.get_tag("_dd.code_origin.type") is None
        assert _exit.get_tag("_dd.code_origin.frames.0.file") is None
        assert _exit.get_tag("_dd.code_origin.frames.0.line") is None

    def test_span_origin_entry_method(self):
        class App:
            def entry_call(self):
                pass

        app = App()

        core.dispatch("service_entrypoint.patch", (app.entry_call,))

        with self.tracer.trace("entry"):
            app.entry_call()
            with self.tracer.trace("middle"):
                with self.tracer.trace("exit", span_type=SpanTypes.HTTP):
                    pass

        self.assert_span_count(3)
        entry, *_ = self.get_spans()

        # Check for the expected tags on the entry span
        assert entry.get_tag("_dd.code_origin.type") == "entry"
        assert entry.get_tag("_dd.code_origin.frames.0.file") == str(Path(__file__).resolve())
        assert entry.get_tag("_dd.code_origin.frames.0.line") == str(App.entry_call.__code__.co_firstlineno)
        assert entry.get_tag("_dd.code_origin.frames.0.type") == __name__
        assert (
            entry.get_tag("_dd.code_origin.frames.0.method")
            == "SpanProbeTestCase.test_span_origin_entry_method.<locals>.App.entry_call"
        )


def test_instrument_view_benchmark(benchmark):
    """Benchmark instrument_view performance when wrapping functions."""
    MockSpanCodeOriginProcessorEntry.enable()

    try:

        def setup():
            """Create a unique function to wrap for each iteration."""

            # Create a more realistic view function similar to Flask views
            # with decorators, imports, and more complex code
            def realistic_view(request_arg, *args, **kwargs):
                """A realistic view function with actual code."""
                import json
                import os  # noqa

                data = {"status": "ok", "items": []}
                for i in range(10):
                    item = {
                        "id": i,
                        "name": f"item_{i}",
                        "value": i * 100,
                    }
                    data["items"].append(item)

                result = json.dumps(data)
                return result

            return (realistic_view,), {}

        # Benchmark the wrapping operation
        benchmark.pedantic(
            MockSpanCodeOriginProcessorEntry.instrument_view,
            setup=setup,
            rounds=100,
        )

    finally:
        MockSpanCodeOriginProcessorEntry.disable()
