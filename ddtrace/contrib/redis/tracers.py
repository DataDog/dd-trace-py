"""
tracers exposed publicly
"""
from redis import StrictRedis
import wrapt

# dogtrace
from ...ext import AppTypes
from .patch import traced_execute_command, traced_pipeline
from ...pin import Pin


DEFAULT_SERVICE = 'redis'


def get_traced_redis(ddtracer, service=DEFAULT_SERVICE, meta=None):
    """ DEPRECATED """
    return _get_traced_redis(ddtracer, StrictRedis, service, meta)


def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    """ DEPRECATED. Use patch* functions instead. """
    return _get_traced_redis(ddtracer, baseclass, service, meta)

def _get_traced_redis(ddtracer, baseclass, service, meta):
    # Inherited class, containing the patched methods
    class TracedRedis(baseclass):
        pass

    setattr(TracedRedis, 'execute_command',
            wrapt.FunctionWrapper(TracedRedis.execute_command, traced_execute_command))
    setattr(TracedRedis, 'pipeline',
            wrapt.FunctionWrapper(TracedRedis.pipeline, traced_pipeline))

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

