from importlib import import_module

from ddtrace import config
from ddtrace._tracing import _limits
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import elasticsearch as metadata
from ...ext import http
from ...internal.compat import urlencode
from ...internal.utils.wrappers import unwrap as _u
from ...pin import Pin
from .quantize import quantize


config._add(
    "elasticsearch",
    {
        "_default_service": "elasticsearch",
    },
)


def _es_modules():
    module_names = (
        "elasticsearch",
        "elasticsearch1",
        "elasticsearch2",
        "elasticsearch5",
        "elasticsearch6",
        "elasticsearch7",
        "opensearchpy",
    )
    for module_name in module_names:
        try:
            yield import_module(module_name)
        except ImportError:
            pass


# NB: We are patching the default elasticsearch.transport module
def patch():
    for elasticsearch in _es_modules():
        _patch(elasticsearch)


def _patch(elasticsearch):
    if getattr(elasticsearch, "_datadog_patch", False):
        return
    setattr(elasticsearch, "_datadog_patch", True)
    _w(elasticsearch.transport, "Transport.perform_request", _get_perform_request(elasticsearch))
    Pin().onto(elasticsearch.transport.Transport)


def unpatch():
    for elasticsearch in _es_modules():
        _unpatch(elasticsearch)


def _unpatch(elasticsearch):
    if getattr(elasticsearch, "_datadog_patch", False):
        setattr(elasticsearch, "_datadog_patch", False)
        _u(elasticsearch.transport.Transport, "perform_request")


def _get_perform_request(elasticsearch):
    def _perform_request(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with pin.tracer.trace(
            "elasticsearch.query", service=ext_service(pin, config.elasticsearch), span_type=SpanTypes.ELASTICSEARCH
        ) as span:
            # set component tag equal to name of integration
            span.set_tag_str("component", config.elasticsearch.integration_name)

            span.set_tag(SPAN_MEASURED_KEY)

            # Don't instrument if the trace is not sampled
            if not span.sampled:
                return func(*args, **kwargs)

            method, url = args
            params = kwargs.get("params") or {}
            encoded_params = urlencode(params)
            body = kwargs.get("body")

            span.set_tag_str(metadata.METHOD, method)
            span.set_tag_str(metadata.URL, url)
            span.set_tag_str(metadata.PARAMS, encoded_params)
            if config.elasticsearch.trace_query_string:
                span.set_tag_str(http.QUERY_STRING, encoded_params)

            if method in ["GET", "POST"]:
                ser_body = instance.serializer.dumps(body)
                # Elasticsearch request bodies can be very large resulting in traces being too large
                # to send.
                # When this occurs, drop the value.
                # Ideally the body should be truncated, however we cannot truncate as the obfuscation
                # logic for the body lives in the agent and truncating would make the body undecodable.
                if len(ser_body) <= _limits.MAX_SPAN_META_VALUE_LEN:
                    span.set_tag_str(metadata.BODY, ser_body)
                else:
                    span.set_tag_str(
                        metadata.BODY,
                        "<body size %s exceeds limit of %s>" % (len(ser_body), _limits.MAX_SPAN_META_VALUE_LEN),
                    )
            status = None

            # set analytics sample rate
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.elasticsearch.get_analytics_sample_rate())

            span = quantize(span)

            try:
                result = func(*args, **kwargs)
            except elasticsearch.exceptions.TransportError as e:
                span.set_tag(http.STATUS_CODE, getattr(e, "status_code", 500))
                span.error = 1
                raise

            try:
                # Optional metadata extraction with soft fail.
                if isinstance(result, tuple) and len(result) == 2:
                    # elasticsearch<2.4; it returns both the status and the body
                    status, data = result
                else:
                    # elasticsearch>=2.4; internal change for ``Transport.perform_request``
                    # that just returns the body
                    data = result

                took = data.get("took")
                if took:
                    span.set_metric(metadata.TOOK, int(took))
            except Exception:
                pass

            if status:
                span.set_tag(http.STATUS_CODE, status)

            return result

    return _perform_request
