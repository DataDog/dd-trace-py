from ddtrace import config
from ddtrace.contrib.internal.redis_utils import _instrument_redis_cmd
from ddtrace.contrib.internal.redis_utils import _instrument_redis_execute_pipeline
from ddtrace.contrib.internal.redis_utils import _run_redis_command_async
from ddtrace.internal.utils.formats import stringify_cache_args


async def instrumented_async_execute_command(func, instance, args, kwargs):
    with _instrument_redis_cmd(config.redis, instance, args) as ctx:
        return await _run_redis_command_async(ctx=ctx, func=func, args=args, kwargs=kwargs)


async def instrumented_async_execute_pipeline(func, instance, args, kwargs):
    cmds = [stringify_cache_args(c, cmd_max_len=config.redis.cmd_max_length) for c, _ in instance.command_stack]
    with _instrument_redis_execute_pipeline(config.redis, cmds, instance):
        return await func(*args, **kwargs)


async def instrumented_async_execute_cluster_pipeline(func, instance, args, kwargs):
    # Redis-py 8 stores cluster pipeline commands in the execution strategy.
    command_stack = getattr(instance, "command_stack", None)
    if not command_stack:
        execution_strategy = getattr(instance, "_execution_strategy", None)
        if execution_strategy is not None:
            command_stack = getattr(execution_strategy, "command_queue", None)
            if command_stack is None:
                command_stack = getattr(execution_strategy, "_command_queue", command_stack)
    if command_stack is None:
        command_stack = getattr(instance, "_command_stack", [])

    cmds = [stringify_cache_args(c.args, cmd_max_len=config.redis.cmd_max_length) for c in command_stack]
    with _instrument_redis_execute_pipeline(config.redis, cmds, instance):
        return await func(*args, **kwargs)
