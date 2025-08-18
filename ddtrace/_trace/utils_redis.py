"""
Some utils used by the dogtrace redis integration
"""

from contextlib import contextmanager
from typing import List
from typing import Optional

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.redis_utils import _extract_conn_tags
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import redis as redisx
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args


format_command_args = stringify_cache_args


def _set_span_tags(
    span, pin, config_integration, args: Optional[List], instance, query: Optional[List], is_cluster: bool = False
):
    span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag_str(COMPONENT, config_integration.integration_name)
    span.set_tag_str(db.SYSTEM, redisx.APP)
    # PERF: avoid setting via Span.set_tag
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    if query is not None:
        span_name = schematize_cache_operation(redisx.RAWCMD, cache_provider=redisx.APP)  # type: ignore[operator]
        span.set_tag_str(span_name, query)
    if pin.tags:
        span.set_tags(pin.tags)
    # some redis clients do not have a connection_pool attribute (ex. aioredis v1.3)
    if not is_cluster and hasattr(instance, "connection_pool"):
        span.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
    if args is not None:
        span.set_metric(redisx.ARGS_LEN, len(args))
    else:
        for attr in ("command_stack", "_command_stack"):
            if hasattr(instance, attr):
                span.set_metric(redisx.PIPELINE_LEN, len(getattr(instance, attr)))
