import time

# 3rd party
from nose.tools import eq_, ok_
from django.core.cache import caches

# testing
from .utils import DjangoTraceTestCase, override_ddtrace_settings
from ...util import assert_dict_issuperset


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
        eq_(span.service, 'django')
        eq_(span.resource, 'get')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'missing_key',
            'env': 'test',
        }

        assert_dict_issuperset(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    @override_ddtrace_settings(INSTRUMENT_CACHE=False)
    def test_cache_disabled(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get('missing_key')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def test_cache_set(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.set('a_new_key', 50)
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'set')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'a_new_key',
            'env': 'test',
        }

        assert_dict_issuperset(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_add(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.add('a_new_key', 50)
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'add')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'a_new_key',
            'env': 'test',
        }

        assert_dict_issuperset(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_delete(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.delete('an_existing_key')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'delete')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'an_existing_key',
            'env': 'test',
        }

        assert_dict_issuperset(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_incr(self):
        # get the default cache, set the value and reset the spans
        cache = caches['default']
        cache.set('value', 0)
        self.tracer.writer.spans = []

        # (trace) the cache miss
        start = time.time()
        hit = cache.incr('value')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)

        span_incr = spans[0]
        span_get = spans[1]

        # LocMemCache doesn't provide an atomic operation
        eq_(span_get.service, 'django')
        eq_(span_get.resource, 'get')
        eq_(span_get.name, 'django.cache')
        eq_(span_get.span_type, 'cache')
        eq_(span_get.error, 0)
        eq_(span_incr.service, 'django')
        eq_(span_incr.resource, 'incr')
        eq_(span_incr.name, 'django.cache')
        eq_(span_incr.span_type, 'cache')
        eq_(span_incr.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'value',
            'env': 'test',
        }

        assert_dict_issuperset(span_get.meta, expected_meta)
        assert_dict_issuperset(span_incr.meta, expected_meta)
        assert start < span_incr.start < span_incr.start + span_incr.duration < end

    def test_cache_decr(self):
        # get the default cache, set the value and reset the spans
        cache = caches['default']
        cache.set('value', 0)
        self.tracer.writer.spans = []

        # (trace) the cache miss
        start = time.time()
        hit = cache.decr('value')
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_decr = spans[0]
        span_incr = spans[1]
        span_get = spans[2]

        # LocMemCache doesn't provide an atomic operation
        eq_(span_get.service, 'django')
        eq_(span_get.resource, 'get')
        eq_(span_get.name, 'django.cache')
        eq_(span_get.span_type, 'cache')
        eq_(span_get.error, 0)
        eq_(span_incr.service, 'django')
        eq_(span_incr.resource, 'incr')
        eq_(span_incr.name, 'django.cache')
        eq_(span_incr.span_type, 'cache')
        eq_(span_incr.error, 0)
        eq_(span_decr.service, 'django')
        eq_(span_decr.resource, 'decr')
        eq_(span_decr.name, 'django.cache')
        eq_(span_decr.span_type, 'cache')
        eq_(span_decr.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'value',
            'env': 'test',
        }

        assert_dict_issuperset(span_get.meta, expected_meta)
        assert_dict_issuperset(span_incr.meta, expected_meta)
        assert_dict_issuperset(span_decr.meta, expected_meta)
        assert start < span_decr.start < span_decr.start + span_decr.duration < end

    def test_cache_get_many(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_get_many = spans[0]
        span_get_first = spans[1]
        span_get_second = spans[2]

        # LocMemCache doesn't provide an atomic operation
        eq_(span_get_first.service, 'django')
        eq_(span_get_first.resource, 'get')
        eq_(span_get_first.name, 'django.cache')
        eq_(span_get_first.span_type, 'cache')
        eq_(span_get_first.error, 0)
        eq_(span_get_second.service, 'django')
        eq_(span_get_second.resource, 'get')
        eq_(span_get_second.name, 'django.cache')
        eq_(span_get_second.span_type, 'cache')
        eq_(span_get_second.error, 0)
        eq_(span_get_many.service, 'django')
        eq_(span_get_many.resource, 'get_many')
        eq_(span_get_many.name, 'django.cache')
        eq_(span_get_many.span_type, 'cache')
        eq_(span_get_many.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': str(['missing_key', 'another_key']),
            'env': 'test',
        }

        assert_dict_issuperset(span_get_many.meta, expected_meta)
        assert start < span_get_many.start < span_get_many.start + span_get_many.duration < end

    def test_cache_set_many(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.set_many({'first_key': 1, 'second_key': 2})
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_set_many = spans[0]
        span_set_first = spans[1]
        span_set_second = spans[2]

        # LocMemCache doesn't provide an atomic operation
        eq_(span_set_first.service, 'django')
        eq_(span_set_first.resource, 'set')
        eq_(span_set_first.name, 'django.cache')
        eq_(span_set_first.span_type, 'cache')
        eq_(span_set_first.error, 0)
        eq_(span_set_second.service, 'django')
        eq_(span_set_second.resource, 'set')
        eq_(span_set_second.name, 'django.cache')
        eq_(span_set_second.span_type, 'cache')
        eq_(span_set_second.error, 0)
        eq_(span_set_many.service, 'django')
        eq_(span_set_many.resource, 'set_many')
        eq_(span_set_many.name, 'django.cache')
        eq_(span_set_many.span_type, 'cache')
        eq_(span_set_many.error, 0)

        eq_(span_set_many.meta['django.cache.backend'], 'django.core.cache.backends.locmem.LocMemCache')
        ok_('first_key' in span_set_many.meta['django.cache.key'])
        ok_('second_key' in span_set_many.meta['django.cache.key'])
        assert start < span_set_many.start < span_set_many.start + span_set_many.duration < end

    def test_cache_delete_many(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        start = time.time()
        hit = cache.delete_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_delete_many = spans[0]
        span_delete_first = spans[1]
        span_delete_second = spans[2]

        # LocMemCache doesn't provide an atomic operation
        eq_(span_delete_first.service, 'django')
        eq_(span_delete_first.resource, 'delete')
        eq_(span_delete_first.name, 'django.cache')
        eq_(span_delete_first.span_type, 'cache')
        eq_(span_delete_first.error, 0)
        eq_(span_delete_second.service, 'django')
        eq_(span_delete_second.resource, 'delete')
        eq_(span_delete_second.name, 'django.cache')
        eq_(span_delete_second.span_type, 'cache')
        eq_(span_delete_second.error, 0)
        eq_(span_delete_many.service, 'django')
        eq_(span_delete_many.resource, 'delete_many')
        eq_(span_delete_many.name, 'django.cache')
        eq_(span_delete_many.span_type, 'cache')
        eq_(span_delete_many.error, 0)

        eq_(span_delete_many.meta['django.cache.backend'], 'django.core.cache.backends.locmem.LocMemCache')
        ok_('missing_key' in span_delete_many.meta['django.cache.key'])
        ok_('another_key' in span_delete_many.meta['django.cache.key'])
        assert start < span_delete_many.start < span_delete_many.start + span_delete_many.duration < end

    @override_ddtrace_settings(CACHE_SERVICE_NAME='modified_cache_name')
    def test_cache_as_named_service(self):
        # get the default cache
        cache = caches['default']
        cache.set('a_new_key', 50)

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'modified_cache_name')
        eq_(span.resource, 'set')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'a_new_key',
            'env': 'test',
        }

        assert_dict_issuperset(span.meta, expected_meta)
