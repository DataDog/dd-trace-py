from pathlib import Path
import typing as t

import ddtrace
from ddtrace.debugging._origin.span import SpanCodeOriginProcessorEntry
from ddtrace.debugging._origin.span import SpanCodeOriginProcessorExit
from ddtrace.debugging._session import Session
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from tests.debugging.mocking import MockLogsIntakeUploaderV1
from tests.utils import TracerTestCase


class MockSpanCodeOriginProcessorEntry(SpanCodeOriginProcessorEntry):
    __uploader__ = MockLogsIntakeUploaderV1

    @classmethod
    def get_uploader(cls) -> MockLogsIntakeUploaderV1:
        return t.cast(MockLogsIntakeUploaderV1, cls.__uploader__._instance)


class MockSpanCodeOriginProcessor(SpanCodeOriginProcessorExit):
    __uploader__ = MockLogsIntakeUploaderV1

    @classmethod
    def get_uploader(cls) -> MockLogsIntakeUploaderV1:
        return t.cast(MockLogsIntakeUploaderV1, cls.__uploader__._instance)


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        MockSpanCodeOriginProcessorEntry.enable()
        MockSpanCodeOriginProcessor.enable()

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

        MockSpanCodeOriginProcessorEntry.disable()
        MockSpanCodeOriginProcessor.disable()
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

        # Check for the expected tags on the exit span
        assert _exit.get_tag("_dd.code_origin.type") == "exit"
        assert _exit.get_tag("_dd.code_origin.frames.0.file") == str(Path(__file__).resolve())
        assert _exit.get_tag("_dd.code_origin.frames.0.line") == str(self.test_span_origin.__code__.co_firstlineno)

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
        payloads = MockSpanCodeOriginProcessor.get_uploader().wait_for_payloads()
        snapshot_ids = {p["debugger"]["snapshot"]["id"] for p in payloads}

        assert len(payloads) == len(snapshot_ids)

        entry_snapshot_id = entry.get_tag("_dd.code_origin.frames.0.snapshot_id")
        assert entry.get_tag("_dd.code_origin.type") == "entry"
        assert entry_snapshot_id in snapshot_ids

        # Check that we don't have span location tags on the middle span
        assert middle.get_tag("_dd.code_origin.frames.0.snapshot_id") is None

        # Check that we have all the snapshots for the exit span
        assert _exit.get_tag("_dd.code_origin.type") == "exit"
        snapshot_ids_from_span_tags = {_exit.get_tag(f"_dd.code_origin.frames.{_}.snapshot_id") for _ in range(8)}
        snapshot_ids_from_span_tags.discard(None)
        assert snapshot_ids_from_span_tags < snapshot_ids

        # Check that we have complete data
        snapshot_ids_from_span_tags.add(entry_snapshot_id)
        assert snapshot_ids_from_span_tags == snapshot_ids

    def test_span_origin_entry(self):
        # Disable the processor to avoid interference with the test
        MockSpanCodeOriginProcessor.disable()

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
