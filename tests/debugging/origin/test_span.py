from pathlib import Path

import ddtrace
from ddtrace.debugging._origin.span import SpanCodeOriginProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from tests.utils import TracerTestCase


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        SpanCodeOriginProcessor.enable()

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

        SpanCodeOriginProcessor.disable()
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
        assert _exit.get_tag("_dd.code_origin.frames.2.file") == str(Path(__file__).resolve())
        assert _exit.get_tag("_dd.code_origin.frames.2.line") == str(self.test_span_origin.__code__.co_firstlineno)
