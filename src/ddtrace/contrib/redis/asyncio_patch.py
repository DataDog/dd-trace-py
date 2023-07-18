from ddtrace import config

from ...internal.utils.formats import stringify_cache_args
from ...pin import Pin
from ..trace_utils_redis import _trace_redis_cmd
from ..trace_utils_redis import _trace_redis_execute_async_cluster_pipeline
from ..trace_utils_redis import _trace_redis_execute_pipeline


#
# tracing async functions
#
async def traced_async_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _trace_redis_cmd(pin, config.redis, instance, args):
        return await func(*args, **kwargs)


async def traced_async_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c, cmd_max_len=config.redis.cmd_max_length) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.redis, resource, instance):
        return await func(*args, **kwargs)


async def traced_async_execute_cluster_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c.args, cmd_max_len=config.redis.cmd_max_length) for c in instance._command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_async_cluster_pipeline(pin, config.redis, resource, instance):
        return await func(*args, **kwargs)
