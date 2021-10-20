import aredis

from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import redis as redisx
from ...pin import Pin
from ...utils.wrappers import unwrap
from ..redis.util import _trace_redis_cmd
from ..redis.util import _trace_redis_execute_pipeline
from ..redis.util import format_command_args


config._add("aredis", dict(_default_service="redis"))


def patch():
    """Patch the instrumented methods"""
    if getattr(aredis, "_datadog_patch", False):
        return
    setattr(aredis, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w("aredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("aredis.client", "StrictRedis.pipeline", traced_pipeline)
    _w("aredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("aredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)
    Pin(service=None, app=redisx.APP).onto(aredis.StrictRedis)


def unpatch():
    if getattr(aredis, "_datadog_patch", False):
        setattr(aredis, "_datadog_patch", False)

        unwrap(aredis.client.StrictRedis, "execute_command")
        unwrap(aredis.client.StrictRedis, "pipeline")
        unwrap(aredis.pipeline.StrictPipeline, "execute")
        unwrap(aredis.pipeline.StrictPipeline, "immediate_execute_command")


#
# tracing functions
#
async def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.aredis, instance, args):
        # run the command
        return await func(*args, **kwargs)


async def traced_pipeline(func, instance, args, kwargs):
    pipeline = await func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


async def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [format_command_args(c) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.aredis, resource, instance):
        return await func(*args, **kwargs)
