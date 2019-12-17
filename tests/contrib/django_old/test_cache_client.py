# 3rd party
from django.core.cache import caches

# testing
from .utils import DjangoTraceTestCase, override_ddtrace_settings


class DjangoCacheWrapperTest(DjangoTraceTestCase):
    """
    Ensures that the cache system is properly traced
    """
    @override_ddtrace_settings(DEFAULT_CACHE_SERVICE='foo')
    def test_cache_service_can_be_overriden(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        cache.get('missing_key')

        # tests
        spans = self.tracer.writer.pop()
        assert len(spans) == 1

        span = spans[0]
        assert span.service == 'foo'

    @override_ddtrace_settings(INSTRUMENT_CACHE=False)
    def test_cache_disabled(self):
        # get the default cache
        cache = caches['default']

        # (trace) the cache miss
        cache.get('missing_key')

        # tests
        spans = self.tracer.writer.pop()
        assert len(spans) == 0
