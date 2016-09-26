import logging


log = logging.getLogger(__name__)


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
    pass
