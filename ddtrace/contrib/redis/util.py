"""
Some utils used by the dogtrace redis integration
"""
from contextlib import contextmanager

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import db
from ...ext import net
from ...ext import redis as redisx
from ...internal.utils.formats import stringify_cache_args


format_command_args = stringify_cache_args

single_key_commands = [
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
multi_key_commands = ["MGET"]
row_returning_commands = single_key_commands + multi_key_commands


def _extract_conn_tags(conn_kwargs):
    """Transform redis conn info into dogtrace metas"""
    try:
        conn_tags = {
            net.TARGET_HOST: conn_kwargs["host"],
            net.TARGET_PORT: conn_kwargs["port"],
            redisx.DB: conn_kwargs.get("db") or 0,
        }
        client_name = conn_kwargs.get("client_name")
        if client_name:
            conn_tags[redisx.CLIENT_NAME] = client_name
        return conn_tags
    except Exception:
        return {}


def determine_row_count(redis_command, span, result):
    empty_results = [b"", [], {}, None]
    # result can be an empty list / dict / string
    if result not in empty_results:
        if redis_command == "MGET":
            # only include valid key results within count
            result = [x for x in result if x not in empty_results]
            span.set_metric(db.ROWCOUNT, len(result))
        elif redis_command == "HMGET":
            # only include valid key results within count
            result = [x for x in result if x not in empty_results]
            span.set_metric(db.ROWCOUNT, 1 if len(result) > 0 else 0)
        else:
            span.set_metric(db.ROWCOUNT, 1)
    else:
        # set count equal to 0 if an empty result
        span.set_metric(db.ROWCOUNT, 0)


def _run_redis_command(span, func, args, kwargs):
    try:
        parsed_command = stringify_cache_args(args)
        redis_command = parsed_command.split(" ")[0]

        result = func(*args, **kwargs)
        return result
    except Exception:
        if redis_command in row_returning_commands:
            span.set_metric(db.ROWCOUNT, 0)
        raise
    finally:
        if redis_command in row_returning_commands:
            determine_row_count(redis_command=redis_command, span=span, result=result)


@contextmanager
def _trace_redis_cmd(pin, config_integration, instance, args):
    """Create a span for the execute command method and tag it"""
    with pin.tracer.trace(
        redisx.CMD, service=trace_utils.ext_service(pin, config_integration), span_type=SpanTypes.REDIS
    ) as span:
        span.set_tag_str("component", config_integration.integration_name)
        span.set_tag(SPAN_MEASURED_KEY)
        query = stringify_cache_args(args)
        span.resource = query
        span.set_tag_str(redisx.RAWCMD, query)
        if pin.tags:
            span.set_tags(pin.tags)
        # some redis clients do not have a connection_pool attribute (ex. aioredis v1.3)
        if hasattr(instance, "connection_pool"):
            span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        span.set_metric(redisx.ARGS_LEN, len(args))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        yield span


@contextmanager
def _trace_redis_execute_pipeline(pin, config_integration, resource, instance):
    """Create a span for the execute pipeline method and tag it"""
    with pin.tracer.trace(
        redisx.CMD,
        resource=resource,
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
    ) as span:
        span.set_tag_str("component", config_integration.integration_name)
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag_str(redisx.RAWCMD, resource)
        span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        span.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span
