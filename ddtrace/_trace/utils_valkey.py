from typing import Any
from typing import Optional
from typing import Union

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.valkey_utils import _extract_conn_tags
from ddtrace.ext import SpanKind
from ddtrace.ext import db
from ddtrace.ext import valkey as valkeyx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.trace import Span


format_command_args = stringify_cache_args


def _set_span_tags(
    span: Span,
    config_integration: Any,
    args: Optional[list],
    instance: Any,
    query: Optional[Union[str, float, int]],
    is_cluster: bool = False,
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
