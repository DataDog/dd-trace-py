from ddtrace import config
from ddtrace._trace.utils_redis import _trace_redis_cmd
from ddtrace._trace.utils_redis import _trace_redis_execute_async_cluster_pipeline
from ddtrace._trace.utils_redis import _trace_redis_execute_pipeline
from ddtrace.contrib.redis_utils import _run_redis_command_async

from ...internal.utils.formats import stringify_cache_args
from ...pin import Pin


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

    cmds = [stringify_cache_args(c, cmd_max_len=config.redis.cmd_max_length) for c, _ in instance.command_stack]
    with _trace_redis_execute_pipeline(pin, config.redis, cmds, instance):
        return await func(*args, **kwargs)


async def traced_async_execute_cluster_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c.args, cmd_max_len=config.redis.cmd_max_length) for c in instance._command_stack]
    with _trace_redis_execute_async_cluster_pipeline(pin, config.redis, cmds, instance):
        return await func(*args, **kwargs)
