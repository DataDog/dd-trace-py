from contextlib import contextmanager
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.ext import redis as redisx
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args


SINGLE_KEY_COMMANDS = [
    "GET",
    "GETDEL",
    "GETEX",
    "GETRANGE",
    "GETSET",
    "LINDEX",
    "LRANGE",
    "RPOP",
    "LPOP",
    "HGET",
    "HGETALL",
    "HKEYS",
    "HMGET",
    "HRANDFIELD",
    "HVALS",
]
MULTI_KEY_COMMANDS = ["MGET"]
ROW_RETURNING_COMMANDS = SINGLE_KEY_COMMANDS + MULTI_KEY_COMMANDS


def determine_row_count(redis_command: str, result: Optional[Union[List, Dict, str]]) -> int:
    empty_results = [b"", [], {}, None]
    # result can be an empty list / dict / string
    if result not in empty_results:
        if redis_command == "MGET":
            # only include valid key results within count
            result = [x for x in result if x not in empty_results]
            return len(result)
        elif redis_command == "HMGET":
            # only include valid key results within count
            result = [x for x in result if x not in empty_results]
            return 1 if len(result) > 0 else 0
        else:
            return 1
    else:
        return 0


async def _run_redis_command_async(ctx: core.ExecutionContext, func, args, kwargs):
    parsed_command = stringify_cache_args(args)
    redis_command = parsed_command.split(" ")[0]
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
            rowcount = determine_row_count(redis_command=redis_command, result=result)
        if redis_command not in ROW_RETURNING_COMMANDS:
            rowcount = None
        core.dispatch("redis.async_command.post", [ctx, rowcount])


@contextmanager
def _instrument_redis_execute_pipeline(pin, config_integration, cmds, instance):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with core.context_with_data(
        "redis.execute_pipeline",
        span_name=schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        resource=resource,
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
        pin=pin,
    ) as ctx:
        core.dispatch("redis.execute_pipeline", [ctx, pin, config_integration, None, instance, cmd_string])
        yield span


@contextmanager
def _instrument_redis_cmd(pin, config_integration, instance, args):
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with core.context_with_data(
        "redis.command",
        span_name=schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        pin=pin,
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
        resource=query.split(" ")[0] if config_integration.resource_only_command else query,
    ) as ctx:
        core.dispatch("redis.execute_pipeline", [ctx, pin, config_integration, args, instance, query])
        yield ctx
