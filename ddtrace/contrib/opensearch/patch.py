import opensearchpy

from ddtrace import config
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import http
from ...ext import opensearch as metadata
from ...internal.compat import urlencode
from ...internal.utils.wrappers import unwrap as _u
from ...pin import Pin
from .quantize import quantize


config._add(
    "opensearch",
    {
        "_default_service": "opensearch",
    },
)


def patch():
    if getattr(opensearchpy, "_datadog_patch", False):
        return
    setattr(opensearchpy, "_datadog_patch", True)
    _w(opensearchpy.transport, "Transport.perform_request", _get_perform_request())
    Pin().onto(opensearchpy.transport.Transport)


def unpatch():
    if getattr(opensearchpy, "_datadog_patch", False):
        setattr(opensearchpy, "_datadog_patch", False)
        _u(opensearchpy.transport.Transport, "perform_request")


def _get_perform_request():
    def _perform_request(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with pin.tracer.trace(
            # Use `SpanTypes.ELASTICSEARCH` until OpenSearch is aliased to DB
            "opensearch.query", service=ext_service(pin, config.opensearch), span_type=SpanTypes.ELASTICSEARCH
        ) as span:
            span.set_tag(SPAN_MEASURED_KEY)

            # Don't instrument if the trace is not sampled
            if not span.sampled:
                return func(*args, **kwargs)

            method, url = args
            params = kwargs.get("params") or {}
            encoded_params = urlencode(params)
            body = kwargs.get("body")

            span.set_tag(metadata.METHOD, method)
            span.set_tag(metadata.URL, url)
            span.set_tag(metadata.PARAMS, encoded_params)
            if config.opensearch.trace_query_string:
                span.set_tag(http.QUERY_STRING, encoded_params)

            if method in ["GET", "POST"]:
                span.set_tag(metadata.BODY, instance.serializer.dumps(body))

            # set analytics sample rate
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.opensearch.get_analytics_sample_rate())

            span = quantize(span)

            try:
                result = func(*args, **kwargs)
            except opensearchpy.exceptions.TransportError as e:
                span.set_tag(http.STATUS_CODE, getattr(e, "status_code", 500))
                span.error = 1
                raise

            try:
                # Optional metadata extraction with soft fail.
                took = result.get("took")
                if took:
                    span.set_metric(metadata.TOOK, int(took))
            except Exception:
                pass

            return result

    return _perform_request
