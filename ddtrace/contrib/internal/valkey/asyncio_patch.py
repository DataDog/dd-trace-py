from ddtrace import config
from ddtrace._trace.utils_valkey import _instrument_valkey_cmd
from ddtrace._trace.utils_valkey import _instrument_valkey_execute_async_cluster_pipeline
from ddtrace._trace.utils_valkey import _instrument_valkey_execute_pipeline
from ddtrace.contrib.internal.valkey_utils import _run_valkey_command_async
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.trace import Pin


async def instrumented_async_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with _instrument_valkey_cmd(pin, config.valkey, instance, args) as ctx:
        return await _run_valkey_command_async(ctx=ctx, func=func, args=args, kwargs=kwargs)


async def instrumented_async_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c, cmd_max_len=config.valkey.cmd_max_length) for c, _ in instance.command_stack]
    with _instrument_valkey_execute_pipeline(pin, config.valkey, cmds, instance):
        return await func(*args, **kwargs)


async def instrumented_async_execute_cluster_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    cmds = [stringify_cache_args(c.args, cmd_max_len=config.valkey.cmd_max_length) for c in instance._command_stack]
    with _instrument_valkey_execute_async_cluster_pipeline(pin, config.valkey, cmds, instance):
        return await func(*args, **kwargs)
