from contextlib import contextmanager
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import redis as redisx
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
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


def _extract_conn_tags(conn_kwargs) -> Dict[str, str]:
    try:
        conn_tags = {
            net.TARGET_HOST: conn_kwargs["host"],
            net.TARGET_PORT: conn_kwargs["port"],
            net.SERVER_ADDRESS: conn_kwargs["host"],
            redisx.DB: conn_kwargs.get("db") or 0,
        }
        client_name = conn_kwargs.get("client_name")
        if client_name:
            conn_tags[redisx.CLIENT_NAME] = client_name
        return conn_tags
    except Exception:
        return {}


def _build_tags(query, pin, instance, integration_name):
    ret = dict()
    ret[SPAN_KIND] = SpanKind.CLIENT
    ret[COMPONENT] = integration_name
    ret[db.SYSTEM] = redisx.APP
    if query is not None:
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)  # type: ignore[operator]
        ret[span_name] = query
    if pin.tags:
        # PERF: avoid Span.set_tag to avoid unnecessary checks
        for key, value in pin.tags.items():
            ret[key] = value
    # some redis clients do not have a connection_pool attribute (ex. aioredis v1.3)
    if hasattr(instance, "connection_pool"):
        for key, value in _extract_conn_tags(instance.connection_pool.connection_kwargs).items():
            ret[key] = value
    return ret


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
        measured=True,
        tags=_build_tags(cmd_string, pin, instance, config_integration.integration_name),
    ) as ctx:
        core.dispatch("redis.execute_pipeline", [ctx, pin, config_integration, None, instance, cmd_string])
        yield ctx.span


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
        measured=True,
        tags=_build_tags(query, pin, instance, config_integration.integration_name),
    ) as ctx:
        core.dispatch("redis.execute_pipeline", [ctx, pin, config_integration, args, instance, query])
        yield ctx
