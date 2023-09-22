from ddtrace import config

from ...ext import db
from ...internal.utils.formats import stringify_cache_args
from ...pin import Pin
from ..trace_utils_redis import ROW_RETURNING_COMMANDS
from ..trace_utils_redis import _trace_redis_cmd
from ..trace_utils_redis import _trace_redis_execute_async_cluster_pipeline
from ..trace_utils_redis import _trace_redis_execute_pipeline
from ..trace_utils_redis import determine_row_count


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
    resource = "\n".join(cmds)
    with _trace_redis_execute_pipeline(pin, config.redis, resource, instance):
        return await func(*args, **kwargs)


async def _run_redis_command_async(span, func, args, kwargs):
    try:
        parsed_command = stringify_cache_args(args)
        redis_command = parsed_command.split(" ")[0]

        result = await func(*args, **kwargs)
        if redis_command in ROW_RETURNING_COMMANDS:
            determine_row_count(redis_command=redis_command, span=span, result=result)
        return result
    except Exception:
        if redis_command in ROW_RETURNING_COMMANDS:
            span.set_metric(db.ROWCOUNT, 0)
        raise


async def traced_async_execute_cluster_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c.args, cmd_max_len=config.redis.cmd_max_length) for c in instance._command_stack]
    resource = "\n".join(cmds)
    with _trace_redis_execute_async_cluster_pipeline(pin, config.redis, resource, instance):
        return await func(*args, **kwargs)
