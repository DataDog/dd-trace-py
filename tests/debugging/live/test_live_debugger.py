from collections import Counter
import typing as t

import ddtrace
from ddtrace.debugging._origin.span import SpanCodeOriginProcessor
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.internal import core
from tests.debugging.mocking import MockLogsIntakeUploaderV1
from tests.debugging.mocking import debugger
from tests.debugging.utils import create_snapshot_function_probe
from tests.debugging.utils import create_trigger_function_probe
from tests.utils import TracerTestCase


class MockSpanCodeOriginProcessor(SpanCodeOriginProcessor):
    __uploader__ = MockLogsIntakeUploaderV1

    @classmethod
    def get_uploader(cls) -> MockLogsIntakeUploaderV1:
        return t.cast(MockLogsIntakeUploaderV1, cls.__uploader__._instance)


class SpanProbeTestCase(TracerTestCase):
    def setUp(self):
        super(SpanProbeTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        MockSpanCodeOriginProcessor.enable()

    def tearDown(self):
        ddtrace.tracer = self.backup_tracer
        super(SpanProbeTestCase, self).tearDown()

        MockSpanCodeOriginProcessor.disable()
        core.reset_listeners(event_id="service_entrypoint.patch")

    def test_live_debugger(self):
        from tests.submod.traced_stuff import entrypoint
        from tests.submod.traced_stuff import traced_entrypoint

        with debugger() as d:
            d.add_probes(
                create_trigger_function_probe(
                    probe_id="trigger-probe",
                    module="tests.submod.traced_stuff",
                    func_qname="entrypoint",
                    session_id="test-session-id",
                    level=2,
                ),
                create_snapshot_function_probe(
                    probe_id="snapshot-probe",
                    module="tests.submod.traced_stuff",
                    func_qname="middle",
                    evaluate_at=ProbeEvalTiming.EXIT,
                    tags={"sessionId": "test-session-id"},
                    rate=0.0,
                ),
            )

            core.dispatch("service_entrypoint.patch", (entrypoint,))

            for _ in range(10):
                traced_entrypoint(self.tracer)

        # Check that the function probe has been triggered always, regardless of
        # the rate limit.
        assert Counter(s.probe.probe_id for s in d.snapshots)["snapshot-probe"] == 10
