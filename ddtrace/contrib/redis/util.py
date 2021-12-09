"""
Some utils used by the dogtrace redis integration
"""
from contextlib import contextmanager

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import net
from ...ext import redis as redisx
from ...internal.compat import stringify


VALUE_PLACEHOLDER = "?"
VALUE_MAX_LEN = 100
VALUE_TOO_LONG_MARK = "..."
CMD_MAX_LEN = 1000


def _extract_conn_tags(conn_kwargs):
    """Transform redis conn info into dogtrace metas"""
    try:
        return {
            net.TARGET_HOST: conn_kwargs["host"],
            net.TARGET_PORT: conn_kwargs["port"],
            redisx.DB: conn_kwargs.get("db") or 0,
        }
    except Exception:
        return {}


def format_command_args(args):
    """Format a command by removing unwanted values

    Restrict what we keep from the values sent (with a SET, HGET, LPUSH, ...):
      - Skip binary content
      - Truncate
    """
    length = 0
    out = []
    for arg in args:
        try:
            cmd = stringify(arg)

            if len(cmd) > VALUE_MAX_LEN:
                cmd = cmd[:VALUE_MAX_LEN] + VALUE_TOO_LONG_MARK

            if length + len(cmd) > CMD_MAX_LEN:
                prefix = cmd[: CMD_MAX_LEN - length]
                out.append("%s%s" % (prefix, VALUE_TOO_LONG_MARK))
                break

            out.append(cmd)
            length += len(cmd)
        except Exception:
            out.append(VALUE_PLACEHOLDER)
            break

    return " ".join(out)


@contextmanager
def _trace_redis_cmd(pin, config_integration, instance, args):
    """Create a span for the execute command method and tag it"""
    with pin.tracer.trace(
        redisx.CMD, service=trace_utils.ext_service(pin, config_integration), span_type=SpanTypes.REDIS
    ) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        query = format_command_args(args)
        span.resource = query
        span.set_tag(redisx.RAWCMD, query)
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
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag(redisx.RAWCMD, resource)
        span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        span.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span
