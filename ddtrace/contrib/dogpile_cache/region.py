import dogpile

from ddtrace.ext import SpanTypes

from ...constants import SPAN_MEASURED_KEY
from ...internal.utils import get_argument_value
from ...pin import Pin


def _wrap_get_create(func, instance, args, kwargs):
    pin = Pin.get_from(dogpile.cache)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    key = get_argument_value(args, kwargs, 0, "key")
    with pin.tracer.trace("dogpile.cache", resource="get_or_create", span_type=SpanTypes.CACHE) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag("key", key)
        span.set_tag("region", instance.name)
        span.set_tag("backend", instance.actual_backend.__class__.__name__)
        return func(*args, **kwargs)


def _wrap_get_create_multi(func, instance, args, kwargs):
    pin = Pin.get_from(dogpile.cache)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    keys = get_argument_value(args, kwargs, 0, "keys")
    with pin.tracer.trace("dogpile.cache", resource="get_or_create_multi", span_type="cache") as span:
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag("keys", keys)
        span.set_tag("region", instance.name)
        span.set_tag("backend", instance.actual_backend.__class__.__name__)
        return func(*args, **kwargs)
