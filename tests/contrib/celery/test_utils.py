import gc

from nose.tools import eq_, ok_

from ddtrace.contrib.celery.util import tags_from_context, propagate_span, retrieve_span

from .base import CeleryBaseTestCase


class CeleryTagsTest(CeleryBaseTestCase):
    """Ensures that Celery doesn't extract too much meta
    data when executing tasks asynchronously.
    """
    def test_tags_from_context(self):
        # it should extract only relevant keys
        context = {
            'correlation_id': '44b7f305',
            'delivery_info': '{"eager": "True"}',
            'eta': 'soon',
            'expires': 'later',
            'hostname': 'localhost',
            'id': '44b7f305',
            'reply_to': '44b7f305',
            'retries': 4,
            'timelimit': ('now', 'later'),
            'custom_meta': 'custom_value',
        }

        metas = tags_from_context(context)
        eq_(metas['celery.correlation_id'], '44b7f305')
        eq_(metas['celery.delivery_info'], '{"eager": "True"}')
        eq_(metas['celery.eta'], 'soon')
        eq_(metas['celery.expires'], 'later')
        eq_(metas['celery.hostname'], 'localhost')
        eq_(metas['celery.id'], '44b7f305')
        eq_(metas['celery.reply_to'], '44b7f305')
        eq_(metas['celery.retries'], 4)
        eq_(metas['celery.timelimit'], ('now', 'later'))
        ok_(metas.get('custom_meta', None) is None)

    def test_tags_from_context_empty_keys(self):
        # it should not extract empty keys
        context = {
            'correlation_id': None,
            'exchange': '',
            'timelimit': (None, None),
            'retries': 0,
        }

        tags = tags_from_context(context)
        eq_({}, tags)
        # edge case: `timelimit` can also be a list of None values
        context = {
            'timelimit': [None, None],
        }

        tags = tags_from_context(context)
        eq_({}, tags)

    def test_span_propagation(self):
        # ensure spans getter and setter works properly
        @self.app.task
        def fn_task():
            return 42

        # propagate and retrieve a Span
        task_id = '7c6731af-9533-40c3-83a9-25b58f0d837f'
        span_before = self.tracer.trace('celery.run')
        propagate_span(fn_task, task_id, span_before)
        span_after = retrieve_span(fn_task, task_id)
        ok_(span_before is span_after)

    def test_memory_leak_safety(self):
        # Spans are shared between signals using a Dictionary (task_id -> span).
        # This test ensures the GC correctly cleans finished spans. If this test
        # fails a memory leak will happen for sure.
        @self.app.task
        def fn_task():
            return 42

        # propagate and finish a Span for `fn_task`
        task_id = '7c6731af-9533-40c3-83a9-25b58f0d837f'
        propagate_span(fn_task, task_id, self.tracer.trace('celery.run'))
        weak_dict = getattr(fn_task, '__dd_task_span')
        ok_(weak_dict.get(task_id))
        # flush data and force the GC
        weak_dict.get(task_id).finish()
        self.tracer.writer.pop()
        self.tracer.writer.pop_traces()
        gc.collect()
        ok_(weak_dict.get(task_id) is None)
