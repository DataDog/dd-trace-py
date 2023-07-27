"""
Some utils used by the dogtrace redis integration
"""
from contextlib import contextmanager

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import redis as redisx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args


format_command_args = stringify_cache_args


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


@contextmanager
def _trace_redis_cmd(pin, config_integration, instance, args):
    """Create a span for the execute command method and tag it"""
    with pin.tracer.trace(
        schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag_str(COMPONENT, config_integration.integration_name)
        span.set_tag_str(db.SYSTEM, redisx.APP)
        span.set_tag(SPAN_MEASURED_KEY)
        query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
        span.resource = query
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)
        span.set_tag_str(span_name, query)
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
def _trace_redis_execute_pipeline(pin, config_integration, resource, instance, is_cluster=False):
    """Create a span for the execute pipeline method and tag it"""
    with pin.tracer.trace(
        schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        resource=resource,
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag_str(COMPONENT, config_integration.integration_name)
        span.set_tag_str(db.SYSTEM, redisx.APP)
        span.set_tag(SPAN_MEASURED_KEY)
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)
        span.set_tag_str(span_name, resource)
        if not is_cluster:
            span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        span.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span


@contextmanager
def _trace_redis_execute_async_cluster_pipeline(pin, config_integration, resource, instance):
    """Create a span for the execute async cluster pipeline method and tag it"""
    with pin.tracer.trace(
        schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        resource=resource,
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag_str(COMPONENT, config_integration.integration_name)
        span.set_tag_str(db.SYSTEM, redisx.APP)
        span.set_tag(SPAN_MEASURED_KEY)
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)
        span.set_tag_str(span_name, resource)
        span.set_metric(redisx.PIPELINE_LEN, len(instance._command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span
