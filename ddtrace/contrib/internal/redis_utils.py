from contextlib import contextmanager

from ddtrace.contrib._events.cache import RedisCommandEvent
from ddtrace.contrib.internal.trace_utils import ROW_RETURNING_COMMANDS
from ddtrace.contrib.internal.trace_utils import determine_kv_store_row_count
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import net
from ddtrace.ext import redis as redisx
from ddtrace.internal import core
from ddtrace.internal.utils.formats import stringify_cache_args


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
            rowcount = determine_kv_store_row_count(redis_command, result)
        if redis_command not in ROW_RETURNING_COMMANDS:
            rowcount = None
        ctx.event.rowcount = rowcount


# TODO: this function is now used only in flask_cache so it
# should be removed
def _extract_conn_tags(conn_kwargs) -> dict[str, str]:
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


@contextmanager
def _instrument_redis_execute_pipeline(config_integration, cmds, instance):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    pipeline_len = None
    for attr in ("command_stack", "_command_stack"):
        if hasattr(instance, attr):
            pipeline_len = len(getattr(instance, attr))

    with core.context_with_event(
        RedisCommandEvent(
            component=config_integration.integration_name,
            integration_config=config_integration,
            resource=resource,
            service=ext_service(None, config_integration),
            cache_provider=redisx.APP,
            command_tag_name=redisx.CMD,
            db_system=redisx.APP,
            query=cmd_string,
            pipeline_len=pipeline_len,
            connection_provider=instance if hasattr(instance, "connection_pool") else None,
        )
    ) as ctx:
        yield ctx.span


@contextmanager
def _instrument_redis_cmd(config_integration, instance, args):
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with core.context_with_event(
        RedisCommandEvent(
            component=config_integration.integration_name,
            integration_config=config_integration,
            resource=query.split(" ")[0] if config_integration.resource_only_command else query,
            service=ext_service(None, config_integration),
            cache_provider=redisx.APP,
            command_tag_name=redisx.CMD,
            db_system=redisx.APP,
            query=query,
            args_len=len(args),
            connection_provider=instance if hasattr(instance, "connection_pool") else None,
        )
    ) as ctx:
        yield ctx
