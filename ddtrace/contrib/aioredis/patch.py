import aioredis

from ddtrace import config
from ddtrace.pin import Pin
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..redis.util import _extract_conn_tags
from ..redis.util import _trace_redis_cmd
from ..redis.util import _trace_redis_execute_pipeline
from ..redis.util import format_command_args


config._add("aioredis", dict(_default_service="redis"))

aioredis_version_str = getattr(aioredis, "__version__", "0.0.0")
aioredis_version = tuple([int(i) for i in aioredis_version_str.split(".")])


def patch():
    if getattr(aioredis, "_datadog_patch", False):
        return
    setattr(aioredis, "_datadog_patch", True)
    pin = Pin()
    if aioredis_version >= (2, 0):
        _w("aioredis.client", "Redis.execute_command", traced_execute_command)
        _w("aioredis.client", "Redis.pipeline", traced_pipeline)
        _w("aioredis.client", "Pipeline.execute", traced_execute_pipeline)
        pin.onto(aioredis.client.Redis)
    else:
        _w("aioredis", "Redis.execute", traced_execute_command)
        pin.onto(aioredis.Redis)


def unpatch():
    if not getattr(aioredis, "_datadog_patch", False):
        return

    setattr(aioredis, "_datadog_patch", False)
    if aioredis_version >= (2, 0):
        _u(aioredis.client.Redis, "execute_command")
        _u(aioredis.client.Redis, "pipeline")
        _u(aioredis.client.Pipeline, "execute")
    else:
        _u(aioredis.Redis, "execute")


async def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.aioredis, args) as span:
        if hasattr(instance, "connection_pool"):
            span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
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
    with _trace_redis_execute_pipeline(pin, config.aioredis, resource, instance) as span:
        if hasattr(instance, "connection_pool"):
            span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        return await func(*args, **kwargs)
