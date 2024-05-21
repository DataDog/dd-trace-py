from pathlib import Path

import ddtrace
from ddtrace.debugging._origin.span import SpanOriginProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.utils.inspection import linenos
from tests.utils import TracerTestCase


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        SpanOriginProcessor.enable()

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

        SpanOriginProcessor.disable()
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

        lines = linenos(entry_call)

        # Check for the expected tags on the entry span
        assert entry.get_tag("_dd.entry_location.file") == str(Path(__file__).resolve())
        assert entry.get_tag("_dd.entry_location.start_line") == str(min(lines))
        assert entry.get_tag("_dd.entry_location.end_line") == str(max(lines))
        assert entry.get_tag("_dd.entry_location.type") == __name__
        assert entry.get_tag("_dd.entry_location.method") == "SpanProbeTestCase.test_span_origin.<locals>.entry_call"

        # Check that we don't have span location tags on the middle span
        assert middle.get_tag("_dd.entry_location.file") is None
        assert middle.get_tag("_dd.exit_location.file") is None

        # Check for the expected tags on the exit span
        assert _exit.get_tag("_dd.exit_location.file") is not None  # TODO: Fix
        assert _exit.get_tag("_dd.exit_location.line") is not None  # TODO: Fix
