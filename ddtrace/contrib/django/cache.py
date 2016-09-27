import logging

from django.conf import settings

from .conf import import_from_string
from ..util import _resource_from_cache_prefix


log = logging.getLogger(__name__)

DATADOG_NAMESPACE = '_datadog_original_{method}'

# standard tags
CACHE_BACKEND = 'django.cache.backend'
CACHE_COMMAND_KEY = 'django.cache.key'


def patch_cache(tracer):
    """
    Function that patches the inner cache system. Because the cache backend
    can have different implementations and connectors, this function must
    handle all possible interactions with the Django cache. What follows
    is currently traced:
        * in-memory cache
        * the cache client wrapper that could use any of the common
          Django supported cache servers (Redis, Memcached, Database, Custom)
    """
    # discover used cache backends
    cache_backends = [cache['BACKEND'] for cache in settings.CACHES.values()]

    def traced_get(self, *args, **kwargs):
        """
        Traces a cache GET operation
        """
        with tracer.trace('django.cache', span_type=AppTypes.cache) as span:
            # update the resource name and tag the cache backend
            span.resource = _resource_from_cache_prefix('GET', self)
            cache_backend = '{}.{}'.format(self.__module__, self.__class__.__name__)
            span.set_tag(CACHE_BACKEND, cache_backend)
            if len(args) > 0:
                span.set_tag(CACHE_COMMAND_KEY, args[0])
            return self._datadog_original_get(*args, **kwargs)

    # trace all backends
    for cache_module in cache_backends:
        cache = import_from_string(cache_module, cache_module)

        # prevent patching each backend more than once
        if hasattr(cache, DATADOG_NAMESPACE.format(method='get')):
            log.debug('{} already traced'.format(cache_module))
            continue

        # store the previous method and patch the backend
        setattr(cache, DATADOG_NAMESPACE.format(method='get'), cache.get)
        cache.get = traced_get
