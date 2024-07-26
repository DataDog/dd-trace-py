import os

import aredis

from ddtrace import config
from ddtrace._trace.utils_redis import _instrument_redis_cmd
from ddtrace._trace.utils_redis import _instrument_redis_execute_pipeline
from ddtrace.contrib.redis_utils import _run_redis_command_async
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...internal.schema import schematize_service_name
from ...internal.utils.formats import CMD_MAX_LEN
from ...internal.utils.formats import asbool
from ...internal.utils.formats import stringify_cache_args
from ...internal.utils.wrappers import unwrap
from ...pin import Pin


config._add(
    "aredis",
    dict(
        _default_service=schematize_service_name("redis"),
        cmd_max_length=int(os.getenv("DD_AREDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        resource_only_command=asbool(os.getenv("DD_REDIS_RESOURCE_ONLY_COMMAND", True)),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(aredis, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    """Patch the instrumented methods"""
    if getattr(aredis, "_datadog_patch", False):
        return
    aredis._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("aredis.client", "StrictRedis.execute_command", _traced_execute_command)
    _w("aredis.client", "StrictRedis.pipeline", _traced_pipeline)
    _w("aredis.pipeline", "StrictPipeline.execute", _traced_execute_pipeline)
    _w("aredis.pipeline", "StrictPipeline.immediate_execute_command", _traced_execute_command)
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
async def _traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _instrument_redis_cmd(pin, config.aredis, instance, args) as ctx:
        return await _run_redis_command_async(ctx=ctx, func=func, args=args, kwargs=kwargs)


async def traced_execute_command(func, instance, args, kwargs):
    deprecate(
        "traced_execute_command is deprecated",
        message="traced_execute_command is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_execute_command(func, instance, args, kwargs)


async def _traced_pipeline(func, instance, args, kwargs):
    pipeline = await func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


async def traced_pipeline(func, instance, args, kwargs):
    deprecate(
        "traced_pipeline is deprecated",
        message="traced_pipeline is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_pipeline(func, instance, args, kwargs)


async def _traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c, cmd_max_len=config.aredis.cmd_max_length) for c, _ in instance.command_stack]
    with _instrument_redis_execute_pipeline(pin, config.aredis, cmds, instance):
        return await func(*args, **kwargs)


async def traced_execute_pipeline(func, instance, args, kwargs):
    deprecate(
        "traced_execute_pipeline is deprecated",
        message="traced_execute_pipeline is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_execute_pipeline(func, instance, args, kwargs)
