from ddtrace import config

from ...internal.utils.formats import stringify_cache_args
from ...pin import Pin
from .util import _run_redis_command_async
from .util import _trace_redis_cmd
from .util import _trace_redis_execute_pipeline


#
# tracing async functions
#
async def traced_async_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.redis, instance, args) as span:
        return await _run_redis_command_async(span=span, func=func, args=args, kwargs=kwargs)


async def traced_async_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.redis, resource, instance):
        return await func(*args, **kwargs)
