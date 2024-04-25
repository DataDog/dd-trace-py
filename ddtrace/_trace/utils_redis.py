"""
Some utils used by the dogtrace redis integration
"""
from contextlib import contextmanager

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.redis_utils import _extract_conn_tags
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import redis as redisx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args


format_command_args = stringify_cache_args


@contextmanager
def _trace_redis_cmd(pin, config_integration, instance, args):
    """Create a span for the execute command method and tag it"""
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with pin.tracer.trace(
        schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        service=trace_utils.ext_service(pin, config_integration),
        span_type=SpanTypes.REDIS,
        resource=query.split(" ")[0] if config_integration.resource_only_command else query,
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag_str(COMPONENT, config_integration.integration_name)
        span.set_tag_str(db.SYSTEM, redisx.APP)
        span.set_tag(SPAN_MEASURED_KEY)
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
def _trace_redis_execute_pipeline(pin, config_integration, cmds, instance, is_cluster=False):
    """Create a span for the execute pipeline method and tag it"""
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

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
        span.set_tag_str(span_name, cmd_string)
        if not is_cluster:
            span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        span.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span


@contextmanager
def _trace_redis_execute_async_cluster_pipeline(pin, config_integration, cmds, instance):
    """Create a span for the execute async cluster pipeline method and tag it"""
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

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
        span.set_tag_str(span_name, cmd_string)
        span.set_metric(redisx.PIPELINE_LEN, len(instance._command_stack))
        # set analytics sample rate if enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config_integration.get_analytics_sample_rate())
        # yield the span in case the caller wants to build on span
        yield span
