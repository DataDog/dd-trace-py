from ddtrace import config

from ...ext import db
from ...internal.utils.formats import stringify_cache_args
from ...pin import Pin
from .util import _trace_redis_cmd
from .util import _trace_redis_execute_pipeline
from .util import determine_row_count
from .util import row_returning_commands


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


async def _run_redis_command_async(span, func, args, kwargs):
    try:
        parsed_command = stringify_cache_args(args)
        redis_command = parsed_command.split(" ")[0]

        result = await func(*args, **kwargs)
        return result
    except Exception:
        if redis_command in row_returning_commands:
            span.set_metric(db.ROWCOUNT, 0)
        raise
    finally:
        if redis_command in row_returning_commands:
            determine_row_count(redis_command=redis_command, span=span, result=result)
