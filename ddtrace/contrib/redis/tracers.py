from redis import StrictRedis

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ...vendor.debtcollector.removals import remove


DEFAULT_SERVICE = "redis"


@remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
def get_traced_redis(ddtracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_redis(ddtracer, StrictRedis, service, meta)


@remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_redis(ddtracer, baseclass, service, meta)


def _get_traced_redis(ddtracer, baseclass, service, meta):
    return baseclass
