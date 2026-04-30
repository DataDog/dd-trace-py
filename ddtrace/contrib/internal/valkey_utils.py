from ddtrace.contrib.internal.trace_utils import ROW_RETURNING_COMMANDS
from ddtrace.contrib.internal.trace_utils import determine_kv_store_row_count
from ddtrace.internal import core
from ddtrace.internal.utils.formats import stringify_cache_args


async def _run_valkey_command_async(ctx: core.ExecutionContext, func, args, kwargs):
    parsed_command = stringify_cache_args(args)
    valkey_command = parsed_command.split(" ")[0]
    rowcount = None
    result = None
    try:
        result = await func(*args, **kwargs)
        return result
    except BaseException:
        rowcount = 0
        raise
    finally:
        if rowcount is None:
            rowcount = determine_kv_store_row_count(valkey_command, result)
        if valkey_command not in ROW_RETURNING_COMMANDS:
            rowcount = None
        ctx.event.rowcount = rowcount
