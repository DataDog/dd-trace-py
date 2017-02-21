import time

# 3rd party
from nose.tools import eq_, ok_
from django.core.cache import caches

# testing
from .utils import DjangoTraceTestCase


class DjangoCacheRedisTest(DjangoTraceTestCase):
    """
    Ensures that the cache system is properly traced in
    different cache backend
    """
    def test_cache_redis_get(self):
        # get the redis cache
        cache = caches['redis']

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
            'django.cache.backend': 'django_redis.cache.RedisCache',
            'django.cache.key': 'missing_key',
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_redis_get_many(self):
        # get the redis cache
        cache = caches['redis']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'get_many')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django_redis.cache.RedisCache',
            'django.cache.key': str(['missing_key', 'another_key']),
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_pylibmc_get(self):
        # get the redis cache
        cache = caches['pylibmc']

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
            'django.cache.backend': 'django.core.cache.backends.memcached.PyLibMCCache',
            'django.cache.key': 'missing_key',
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_pylibmc_get_many(self):
        # get the redis cache
        cache = caches['pylibmc']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'get_many')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.memcached.PyLibMCCache',
            'django.cache.key': str(['missing_key', 'another_key']),
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_memcached_get(self):
        # get the redis cache
        cache = caches['python_memcached']

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
            'django.cache.backend': 'django.core.cache.backends.memcached.MemcachedCache',
            'django.cache.key': 'missing_key',
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_memcached_get_many(self):
        # get the redis cache
        cache = caches['python_memcached']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'get_many')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.memcached.MemcachedCache',
            'django.cache.key': str(['missing_key', 'another_key']),
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_django_pylibmc_get(self):
        # get the redis cache
        cache = caches['django_pylibmc']

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
            'django.cache.backend': 'django_pylibmc.memcached.PyLibMCCache',
            'django.cache.key': 'missing_key',
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end

    def test_cache_django_pylibmc_get_many(self):
        # get the redis cache
        cache = caches['django_pylibmc']

        # (trace) the cache miss
        start = time.time()
        hit = cache.get_many(['missing_key', 'another_key'])
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.service, 'django')
        eq_(span.resource, 'get_many')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 0)

        expected_meta = {
            'django.cache.backend': 'django_pylibmc.memcached.PyLibMCCache',
            'django.cache.key': str(['missing_key', 'another_key']),
            'env': 'test',
        }

        eq_(span.meta, expected_meta)
        assert start < span.start < span.start + span.duration < end
