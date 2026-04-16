"""
Some utils used by the dogtrace valkey integration
"""

from contextlib import contextmanager
from typing import Optional

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.valkey_utils import _extract_conn_tags
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import valkey as valkeyx
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.trace import tracer


format_command_args = stringify_cache_args


def _set_span_tags(
    span, config_integration, args: Optional[list], instance, query: Optional[list], is_cluster: bool = False
):
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(COMPONENT, config_integration.integration_name)
    span._set_attribute(db.SYSTEM, valkeyx.APP)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)
    if query is not None:
        span_name = schematize_cache_operation(valkeyx.RAWCMD, cache_provider=valkeyx.APP)  # type: ignore[operator]
        span._set_attribute(span_name, query)
    # some valkey clients do not have a connection_pool attribute (ex. aiovalkey v1.3)
    if not is_cluster and hasattr(instance, "connection_pool"):
        span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
    if args is not None:
        span._set_attribute(valkeyx.ARGS_LEN, len(args))
    else:
        for attr in ("command_stack", "_command_stack"):
            if hasattr(instance, attr):
                span._set_attribute(valkeyx.PIPELINE_LEN, len(getattr(instance, attr)))


@contextmanager
def _instrument_valkey_cmd(config_integration, instance, args):
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with (
        core.context_with_data(
            "valkey.command",
            span_name=schematize_cache_operation(valkeyx.CMD, cache_provider=valkeyx.APP),
            service=trace_utils.ext_service(None, config_integration),
            span_type=SpanTypes.VALKEY,
            resource=query.split(" ")[0] if config_integration.resource_only_command else query,
        ) as ctx,
        ctx.span as span,
    ):
        _set_span_tags(span, config_integration, args, instance, query)
        yield ctx


@contextmanager
def _instrument_valkey_execute_pipeline(config_integration, cmds, instance, is_cluster=False):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with tracer.trace(
        schematize_cache_operation(valkeyx.CMD, cache_provider=valkeyx.APP),
        resource=resource,
        service=trace_utils.ext_service(None, config_integration),
        span_type=SpanTypes.VALKEY,
    ) as span:
        _set_span_tags(span, config_integration, None, instance, cmd_string)
        yield span


@contextmanager
def _instrument_valkey_execute_async_cluster_pipeline(config_integration, cmds, instance):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with tracer.trace(
        schematize_cache_operation(valkeyx.CMD, cache_provider=valkeyx.APP),
        resource=resource,
        service=trace_utils.ext_service(None, config_integration),
        span_type=SpanTypes.VALKEY,
    ) as span:
        _set_span_tags(span, config_integration, None, instance, cmd_string)
        yield span
