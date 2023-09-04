import os

import yaaredis

from ddtrace import config
from ddtrace.vendor import wrapt

from ...internal.schema import schematize_service_name
from ...internal.utils.formats import CMD_MAX_LEN
from ...internal.utils.formats import stringify_cache_args
from ...internal.utils.wrappers import unwrap
from ...pin import Pin
from ..redis.asyncio_patch import _run_redis_command_async
from ..trace_utils_redis import _trace_redis_cmd
from ..trace_utils_redis import _trace_redis_execute_pipeline


config._add(
    "yaaredis",
    dict(
        _default_service=schematize_service_name("redis"),
        cmd_max_length=int(os.getenv("DD_YAAREDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(yaaredis, "__version__", "")


def patch():
    """Patch the instrumented methods"""
    if getattr(yaaredis, "_datadog_patch", False):
        return
    yaaredis._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("yaaredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("yaaredis.client", "StrictRedis.pipeline", traced_pipeline)
    _w("yaaredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("yaaredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)
    Pin().onto(yaaredis.StrictRedis)


def unpatch():
    if getattr(yaaredis, "_datadog_patch", False):
        yaaredis._datadog_patch = False

        unwrap(yaaredis.client.StrictRedis, "execute_command")
        unwrap(yaaredis.client.StrictRedis, "pipeline")
        unwrap(yaaredis.pipeline.StrictPipeline, "execute")
        unwrap(yaaredis.pipeline.StrictPipeline, "immediate_execute_command")


async def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.yaaredis, instance, args) as span:
        return await _run_redis_command_async(span=span, func=func, args=args, kwargs=kwargs)


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

    cmds = [stringify_cache_args(c, cmd_max_len=config.yaaredis.cmd_max_length) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.yaaredis, resource, instance):
        return await func(*args, **kwargs)
