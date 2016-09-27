import time

# 3rd party
from nose.tools import eq_
from django.core.cache import caches

# testing
from .utils import DjangoTraceTestCase


class DjangoCacheWrapperTest(DjangoTraceTestCase):
    """
    Ensures that the cache system is properly traced
    """
    def test_cache_get(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get('missing_key')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.resource, 'get')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'missing_key',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end
