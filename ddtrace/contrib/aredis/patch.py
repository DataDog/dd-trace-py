import os

import aredis
import wrapt

from ddtrace import config

from ...internal.schema import schematize_service_name
from ...internal.utils.formats import CMD_MAX_LEN
from ...internal.utils.formats import stringify_cache_args
from ...internal.utils.wrappers import unwrap
from ...pin import Pin
from ..redis.asyncio_patch import _run_redis_command_async
from ..trace_utils_redis import _trace_redis_cmd
from ..trace_utils_redis import _trace_redis_execute_pipeline


config._add(
    "aredis",
    dict(
        _default_service=schematize_service_name("redis"),
        cmd_max_length=int(os.getenv("DD_AREDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(aredis, "__version__", "")


def patch():
    """Patch the instrumented methods"""
    if getattr(aredis, "_datadog_patch", False):
        return
    aredis._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("aredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("aredis.client", "StrictRedis.pipeline", traced_pipeline)
    _w("aredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("aredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)
    Pin(service=None).onto(aredis.StrictRedis)


def unpatch():
    if getattr(aredis, "_datadog_patch", False):
        aredis._datadog_patch = False

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

    with _trace_redis_cmd(pin, config.aredis, instance, args) as span:
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

    cmds = [stringify_cache_args(c, cmd_max_len=config.aredis.cmd_max_length) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.aredis, resource, instance):
        return await func(*args, **kwargs)
