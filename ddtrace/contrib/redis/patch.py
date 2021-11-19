import redis

from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import redis as redisx
from ...pin import Pin
from ..trace_utils import unwrap
from .util import _trace_redis_cmd
from .util import _trace_redis_execute_pipeline
from .util import format_command_args


config._add("redis", dict(_default_service="redis"))


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Redis and StrictRedis. However, it means that any "import redis.Redis" won't be instrumented.
    """
    if getattr(redis, "_datadog_patch", False):
        return
    setattr(redis, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    if redis.VERSION < (3, 0, 0):
        _w("redis", "StrictRedis.execute_command", traced_execute_command)
        _w("redis", "StrictRedis.pipeline", traced_pipeline)
        _w("redis", "Redis.pipeline", traced_pipeline)
        _w("redis.client", "BasePipeline.execute", traced_execute_pipeline)
        _w("redis.client", "BasePipeline.immediate_execute_command", traced_execute_command)
    else:
        _w("redis", "Redis.execute_command", traced_execute_command)
        _w("redis", "Redis.pipeline", traced_pipeline)
        _w("redis.client", "Pipeline.execute", traced_execute_pipeline)
        _w("redis.client", "Pipeline.immediate_execute_command", traced_execute_command)
    Pin(service=None, app=redisx.APP).onto(redis.StrictRedis)


def unpatch():
    if getattr(redis, "_datadog_patch", False):
        setattr(redis, "_datadog_patch", False)

        if redis.VERSION < (3, 0, 0):
            unwrap(redis.StrictRedis, "execute_command")
            unwrap(redis.StrictRedis, "pipeline")
            unwrap(redis.Redis, "pipeline")
            unwrap(redis.client.BasePipeline, "execute")
            unwrap(redis.client.BasePipeline, "immediate_execute_command")
        else:
            unwrap(redis.Redis, "execute_command")
            unwrap(redis.Redis, "pipeline")
            unwrap(redis.client.Pipeline, "execute")
            unwrap(redis.client.Pipeline, "immediate_execute_command")


#
# tracing functions
#
def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.redis, instance, args):
        return func(*args, **kwargs)


def traced_pipeline(func, instance, args, kwargs):
    pipeline = func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    cmds = [format_command_args(c) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.redis, resource, instance):
        return func(*args, **kwargs)
