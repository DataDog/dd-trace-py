import aioredis

from ddtrace import config
from ddtrace.ext import SpanTypes  # noqa
from ddtrace.pin import Pin
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


config._add("aioredis", dict(_default_service="redis"))


def patch():
    if getattr(aioredis, "_datadog_patch", False):
        return
    setattr(aioredis, "_datadog_patch", True)
    _w("aioredis.client", "Redis.execute_command", traced_execute_command)
    _w("aioredis.client", "Redis.pipeline", traced_pipeline)
    _w("aioredis.client", "Pipeline.execute", traced_execute_pipeline)
    pin = Pin(app="aioredis")
    pin.onto(aioredis.client)


def unpatch():
    if not getattr(aioredis, "_datadog_patch", False):
        return

    setattr(aioredis, "_datadog_patch", False)
    _u(aioredis.client.Redis, "execute_command")
    _u(aioredis.client.Redis, "pipeline")
    _u(aioredis.client.Pipeline, "execute")


# should these share a source with aredis?
async def traced_execute_command():
    pass


async def traced_pipeline():
    pass


async def traced_execute_pipeline():
    pass
