from contextlib import contextmanager
from typing import Any
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
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args


log = get_logger(__name__)


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


def determine_row_count(redis_command: str, result: Optional[Union[list, dict, str]]) -> int:
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
        core.dispatch("redis.async_command.post", (ctx, rowcount))


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


def _extract_cluster_conn_tags(instance) -> dict:
    # startup_nodes is a list in redis-py 4.x and a dict keyed by "host:port" in 5.x;
    # first node is used as the representative host.
    # Node values may be ClusterNode objects (.host/.port) or plain dicts depending on the version.
    try:
        startup_nodes = instance.nodes_manager.startup_nodes

        if isinstance(startup_nodes, dict):
            first_node = next(iter(startup_nodes.values()), None)
        else:
            first_node = startup_nodes[0] if startup_nodes else None

        if not first_node:
            return {}

        if isinstance(first_node, dict):
            host = first_node.get("host")
            port = first_node.get("port")
        else:
            host = first_node.host
            port = first_node.port

        if not host:
            return {}

        return {
            net.TARGET_HOST: host,
            net.TARGET_PORT: port,
            net.SERVER_ADDRESS: host,
            redisx.DB: 0,
        }
    except Exception:
        log.debug("Failed to extract cluster connection tags", exc_info=True)
        return {}


def _build_tags(query, instance, integration_name):
    ret = dict()
    ret[SPAN_KIND] = SpanKind.CLIENT
    ret[COMPONENT] = integration_name
    ret[db.SYSTEM] = redisx.APP
    if query is not None:
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)  # type: ignore[operator]
        ret[span_name] = query
    if hasattr(instance, "connection_pool"):
        for key, value in _extract_conn_tags(instance.connection_pool.connection_kwargs).items():
            ret[key] = value
    elif hasattr(instance, "nodes_manager"):
        # RedisCluster has no connection_pool; extract peer tags from nodes_manager instead.
        ret.update(_extract_cluster_conn_tags(instance))
    return ret


def _get_cluster_pipeline_commands(instance: Any, cmd_max_len: int) -> list[str]:
    """Return a list of stringified commands from a ClusterPipeline instance.

    redis-py reorganized ClusterPipeline internals between 6.x and 7.x/8.x:
      - redis 4.x/5.x/6.x: commands live on ``instance.command_stack`` as ``PipelineCommand``
        objects with an ``.args`` attribute.
      - redis 7.x/8.x: ``instance.command_stack`` is initialized empty (sync) or absent (async);
        commands live on ``instance._execution_strategy._command_queue`` as ``PipelineCommand``
        objects with an ``.args`` attribute.
      - Very old redis versions used ``instance._command_stack`` on the async ClusterPipeline.

    Resolve in that order so all supported redis-py versions (>=4.6.0, <=8.0.1) produce
    correct ``redis.pipeline_length`` and command tag values.
    """
    command_stack = getattr(instance, "command_stack", None)
    if not command_stack:
        execution_strategy = getattr(instance, "_execution_strategy", None)
        if execution_strategy is not None:
            command_stack = getattr(execution_strategy, "_command_queue", None)
    if not command_stack:
        command_stack = getattr(instance, "_command_stack", [])
    return [stringify_cache_args(c.args, cmd_max_len=cmd_max_len) for c in command_stack]


@contextmanager
def _instrument_redis_execute_pipeline(config_integration, cmds, instance):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with core.context_with_data(
        "redis.execute_pipeline",
        span_name=schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        resource=resource,
        service=trace_utils.ext_service(None, config_integration),
        span_type=SpanTypes.REDIS,
        measured=True,
        tags=_build_tags(cmd_string, instance, config_integration.integration_name),
        integration_config=config_integration,
    ) as ctx:
        core.dispatch("redis.execute_pipeline", (ctx, config_integration, None, instance, cmd_string))
        yield ctx.span


@contextmanager
def _instrument_redis_cmd(config_integration, instance, args):
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with core.context_with_data(
        "redis.command",
        span_name=schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        service=trace_utils.ext_service(None, config_integration),
        span_type=SpanTypes.REDIS,
        resource=query.split(" ")[0] if config_integration.resource_only_command else query,
        measured=True,
        tags=_build_tags(query, instance, config_integration.integration_name),
        integration_config=config_integration,
    ) as ctx:
        core.dispatch("redis.execute_pipeline", (ctx, config_integration, args, instance, query))
        yield ctx
