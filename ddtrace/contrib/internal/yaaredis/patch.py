import os
from typing import Dict

import wrapt
import yaaredis

from ddtrace import config
from ddtrace._trace.utils_redis import _instrument_redis_cmd
from ddtrace._trace.utils_redis import _instrument_redis_execute_pipeline
from ddtrace.contrib.internal.redis_utils import _run_redis_command_async
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.formats import CMD_MAX_LEN
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import Pin
from ddtrace.vendor.debtcollector import deprecate


config._add(
    "yaaredis",
    dict(
        _default_service=schematize_service_name("redis"),
        cmd_max_length=int(os.getenv("DD_YAAREDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        resource_only_command=asbool(os.getenv("DD_REDIS_RESOURCE_ONLY_COMMAND", True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(yaaredis, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"yaaredis": ">=2.0.0"}


def patch():
    """Patch the instrumented methods"""
    deprecate(
        prefix="The yaaredis module is deprecated.",
        message="The yaaredis module is deprecated and will be deleted.",
        category=DDTraceDeprecationWarning,
        removal_version="3.0.0",
    )

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

    with _instrument_redis_cmd(pin, config.yaaredis, instance, args) as ctx:
        return await _run_redis_command_async(ctx=ctx, func=func, args=args, kwargs=kwargs)


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
    with _instrument_redis_execute_pipeline(pin, config.yaaredis, cmds, instance):
        return await func(*args, **kwargs)
