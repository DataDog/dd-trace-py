import logging
from redis import StrictRedis


DEFAULT_SERVICE = 'redis'


def get_traced_redis(ddtracer, service=DEFAULT_SERVICE, meta=None):
    """ DEPRECATED """
    logging.warn("deprecated!")
    return _get_traced_redis(ddtracer, StrictRedis, service, meta)


def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    """ DEPRECATED. Use patch* functions instead. """
    logging.warn("deprecated!")
    return _get_traced_redis(ddtracer, baseclass, service, meta)

def _get_traced_redis(ddtracer, baseclass, service, meta):
    return baseclass

