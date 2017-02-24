from unittest import TestCase
from nose.tools import eq_, ok_

from ddtrace.contrib.celery.util import meta_from_context


class CeleryTagsTest(TestCase):
    """
    Ensures that Celery doesn't extract too much meta
    data when executing tasks asynchronously.
    """
    def test_meta_from_context(self):
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

        metas = meta_from_context(context)
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

    def test_meta_from_context_empty_keys(self):
        # it should not extract empty keys
        context = {
            'correlation_id': None,
            'timelimit': (None, None),
            'retries': 0,
        }

        metas = meta_from_context(context)
        eq_({}, metas)
