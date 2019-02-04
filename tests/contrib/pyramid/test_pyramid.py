from nose.tools import eq_

from .utils import PyramidTestCase, PyramidBase


def includeme(config):
    pass


class TestPyramid(PyramidTestCase):
    instrument = True

    def test_tween_overridden(self):
        # in case our tween is overriden by the user config we should
        # not log rendering
        self.override_settings({'pyramid.tweens': 'pyramid.tweens.excview_tween_factory'})
        self.app.get('/json', status=200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)


class TestPyramidDistributedTracing(PyramidBase):
    instrument = True

    def get_settings(self):
        return {
            'datadog_distributed_tracing': True,
        }

    def test_distributed_tracing(self):
        # ensure the Context is properly created
        # if distributed tracing is enabled
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }
        self.app.get('/', headers=headers, status=200)
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        # check the propagated Context
        span = spans[0]
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.get_metric('_sampling_priority_v1'), 2)
