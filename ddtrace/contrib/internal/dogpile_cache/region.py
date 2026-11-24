from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.internal import trace_utils
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import tracer


def _wrap_get_create(func, instance, args, kwargs):
    # TODO: remove when switching to core API
    if not is_tracing_enabled():
        return func(*args, **kwargs)
    key = get_argument_value(args, kwargs, 0, "key")
    with tracer.trace(
        schematize_cache_operation("dogpile.cache", cache_provider="dogpile"),
        resource="get_or_create",
        span_type=SpanTypes.CACHE,
    ) as span:
        set_service_and_source(span, trace_utils.int_service(None, config.dogpile_cache), config.dogpile_cache)
        span._set_attribute(COMPONENT, config.dogpile_cache.integration_name)
        span._set_attribute(_SPAN_MEASURED_KEY, 1)
        span.set_tag("key", key)
        span.set_tag("region", instance.name)
        span.set_tag("backend", instance.actual_backend.__class__.__name__)
        response = func(*args, **kwargs)
        span._set_attribute(db.ROWCOUNT, 1)
        return response


def _wrap_get_create_multi(func, instance, args, kwargs):
    # TODO: remove when switching to core API
    if not is_tracing_enabled():
        return func(*args, **kwargs)

    keys = get_argument_value(args, kwargs, 0, "keys")
    with tracer.trace(
        schematize_cache_operation("dogpile.cache", cache_provider="dogpile"),
        resource="get_or_create_multi",
        span_type="cache",
    ) as span:
        set_service_and_source(span, trace_utils.int_service(None, config.dogpile_cache), config.dogpile_cache)
        span._set_attribute(COMPONENT, config.dogpile_cache.integration_name)
        span._set_attribute(_SPAN_MEASURED_KEY, 1)
        span.set_tag("keys", keys)
        span.set_tag("region", instance.name)
        span.set_tag("backend", instance.actual_backend.__class__.__name__)
        response = func(*args, **kwargs)
        span._set_attribute(db.ROWCOUNT, len(response))
        return response
