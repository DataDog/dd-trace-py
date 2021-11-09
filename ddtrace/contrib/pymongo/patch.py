import contextlib

import pymongo

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import mongo as mongox
from ...internal.utils.deprecation import deprecated
from ..trace_utils import unwrap as _u
from .client import TracedMongoClient
from .client import set_address_tags


config._add(
    "pymongo",
    dict(
        _default_service=mongox.SERVICE,
    ),
)


# Original Client class
_MongoClient = pymongo.MongoClient


def patch():
    patch_pymongo_module()
    # We should progressively get rid of TracedMongoClient. We now try to
    # wrap methods individually. cf #1501
    setattr(pymongo, "MongoClient", TracedMongoClient)


def unpatch():
    unpatch_pymongo_module()
    setattr(pymongo, "MongoClient", _MongoClient)


@deprecated(message="Use patching instead (see the docs).", version="1.0.0")
def trace_mongo_client(client, tracer, service=mongox.SERVICE):
    traced_client = TracedMongoClient(client)
    Pin(service=service, tracer=tracer).onto(traced_client)
    return traced_client


def patch_pymongo_module():
    if getattr(pymongo, "_datadog_patch", False):
        return
    setattr(pymongo, "_datadog_patch", True)
    Pin(app=mongox.SERVICE).onto(pymongo.server.Server)

    # Whenever a pymongo command is invoked, the lib either:
    # - Creates a new socket & performs a TCP handshake
    # - Grabs a socket already initialized before
    _w("pymongo.server", "Server.get_socket", traced_get_socket)


def unpatch_pymongo_module():
    if not getattr(pymongo, "_datadog_patch", False):
        return
    setattr(pymongo, "_datadog_patch", False)

    _u(pymongo.server.Server, "get_socket")


@contextlib.contextmanager
def traced_get_socket(wrapped, instance, args, kwargs):
    pin = Pin._find(wrapped, instance)
    if not pin or not pin.enabled():
        with wrapped(*args, **kwargs) as sock_info:
            yield sock_info
            return

    with pin.tracer.trace(
        "pymongo.get_socket", service=trace_utils.int_service(pin, config.pymongo), span_type=SpanTypes.MONGODB
    ) as span:
        with wrapped(*args, **kwargs) as sock_info:
            set_address_tags(span, sock_info.address)
            span.set_tag(SPAN_MEASURED_KEY)
            yield sock_info
