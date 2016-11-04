"""
tracers exposed publicly
"""
# stdlib

from redis import StrictRedis

# dogtrace
from ...ext import AppTypes
from .patch import patch_target
from ...pin import Pin


DEFAULT_SERVICE = 'redis'


def get_traced_redis(ddtracer, service=DEFAULT_SERVICE, meta=None):
    """ DEPRECATED """
    return _get_traced_redis(ddtracer, StrictRedis, service, meta)


def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_redis(ddtracer, baseclass, service, meta)

def _get_traced_redis(ddtracer, baseclass, service, meta):

    class TracedRedis(baseclass):
        pass

    patch_target(TracedRedis)

    Pin(
        service=service,
        app="redis",
        tags=meta,
        tracer=ddtracer).onto(TracedRedis)

    # set the service info.
    # FIXME[matt] roll this into pin creation
    ddtracer.set_service_info(
        service=service,
        app="redis",
        app_type=AppTypes.db,
    )

    return TracedRedis

