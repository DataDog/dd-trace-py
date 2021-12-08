import yaaredis

from ddtrace import config
from ddtrace.vendor import wrapt

from ...internal.utils.wrappers import unwrap
from ...pin import Pin
from ..redis.util import _trace_redis_cmd
from ..redis.util import _trace_redis_execute_pipeline
from ..redis.util import format_command_args


config._add("yaaredis", dict(_default_service="redis"))


def patch():
    """Patch the instrumented methods"""
    if getattr(yaaredis, "_datadog_patch", False):
        return
    setattr(yaaredis, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w("yaaredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("yaaredis.client", "StrictRedis.pipeline", traced_pipeline)
    _w("yaaredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("yaaredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)
    Pin().onto(yaaredis.StrictRedis)


def unpatch():
    if getattr(yaaredis, "_datadog_patch", False):
        setattr(yaaredis, "_datadog_patch", False)

        unwrap(yaaredis.client.StrictRedis, "execute_command")
        unwrap(yaaredis.client.StrictRedis, "pipeline")
        unwrap(yaaredis.pipeline.StrictPipeline, "execute")
        unwrap(yaaredis.pipeline.StrictPipeline, "immediate_execute_command")


async def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.yaaredis, instance, args):
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
    with _trace_redis_execute_pipeline(pin, config.yaaredis, resource, instance):
        return await func(*args, **kwargs)
