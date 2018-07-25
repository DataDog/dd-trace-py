import pytest
from nose.tools import eq_

from opentracing.scope_managers.tornado import TornadoScopeManager
from tests.opentracer.test_tracer import get_dummy_ot_tracer


def get_dummy_tornado_tracer():
    from ddtrace.contrib.tornado import context_provider

    return get_dummy_ot_tracer(
        "tornado_svc", {}, TornadoScopeManager(), context_provider
    )


@pytest.fixture()
def nop_tracer():
    return get_dummy_tornado_tracer()


class TestTracerTornado():
    """
    Since the ScopeManager is provided by OpenTracing we should simply test
    whether it exists and works for a very simple use-case.
    """

    def test_sanity(self, nop_tracer):
        with nop_tracer.start_active_span('one'):
            with nop_tracer.start_active_span('two'):
                pass

        traces = nop_tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('one', traces[0][0].name)
        eq_('two', traces[0][1].name)
        # the parenting is correct
        eq_(traces[0][0], traces[0][1]._parent)
        eq_(traces[0][0].trace_id, traces[0][1].trace_id)
