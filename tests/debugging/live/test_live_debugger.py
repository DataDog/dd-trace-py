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
                    tags={"session_id": "test-session-id"},
                    rate=0.0,
                ),
            )

            core.dispatch("service_entrypoint.patch", (entrypoint,))

            for _ in range(10):
                traced_entrypoint(self.tracer)

        # Check that the function probe has been triggered always, regardless of
        # the rate limit.
        assert Counter(s.probe.probe_id for s in d.snapshots)["snapshot-probe"] == 10

    def test_live_debugger_budget(self):
        from tests.submod.traced_stuff import middle
        from tests.submod.traced_stuff import traced_entrypoint

        with debugger() as d:
            snapshot_probe = create_snapshot_function_probe(
                probe_id="snapshot-probe",
                module="tests.submod.traced_stuff",
                func_qname="middle",
                evaluate_at=ProbeEvalTiming.EXIT,
                tags={"session_id": "test-session-id"},
                rate=0.0,
            )
            d.add_probes(
                create_trigger_function_probe(
                    probe_id="trigger-probe",
                    module="tests.submod.traced_stuff",
                    func_qname="entrypoint",
                    session_id="test-session-id",
                    level=2,
                ),
                snapshot_probe,
            )

            n_calls = 2 * snapshot_probe.__budget__
            with self.tracer.trace("test") as trace:
                traced_entrypoint(self.tracer)
                for _ in range(n_calls):
                    middle(self.tracer)

            # Check that we cap at the budget of the probe.
            assert Counter(s.probe.probe_id for s in d.snapshots)["snapshot-probe"] == snapshot_probe.__budget__

            # There is an extra call from traced_entrypoint
            assert trace.get_metric("_dd.ld.probe_id.snapshot-probe") == n_calls + 1
