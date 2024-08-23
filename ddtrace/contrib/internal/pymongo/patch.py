import contextlib

import pymongo
from wrapt import wrap_function_wrapper as _w

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo
from ddtrace.internal.constants import COMPONENT

from .client import TracedMongoClient
from .client import set_address_tags
from .client import wrapped_validate_session


config._add(
    "pymongo",
    dict(_default_service="pymongo"),
)


def get_version():
    # type: () -> str
    return getattr(pymongo, "__version__", "")


# Original Client class
_MongoClient = pymongo.MongoClient

_VERSION = pymongo.version_tuple
_CHECKOUT_FN_NAME = "get_socket" if _VERSION < (4, 5) else "checkout"
_VERIFY_VERSION_CLASS = pymongo.pool.SocketInfo if _VERSION < (4, 5) else pymongo.pool.Connection


def patch():
    patch_pymongo_module()
    # We should progressively get rid of TracedMongoClient. We now try to
    # wrap methods individually. cf #1501
    pymongo.MongoClient = TracedMongoClient


def unpatch():
    unpatch_pymongo_module()
    pymongo.MongoClient = _MongoClient


def patch_pymongo_module():
    if getattr(pymongo, "_datadog_patch", False):
        return
    pymongo._datadog_patch = True
    Pin().onto(pymongo.server.Server)

    # Whenever a pymongo command is invoked, the lib either:
    # - Creates a new socket & performs a TCP handshake
    # - Grabs a socket already initialized before
    _w("pymongo.server", "Server.%s" % _CHECKOUT_FN_NAME, traced_get_socket)
    _w("pymongo.pool", f"{_VERIFY_VERSION_CLASS.__name__}.validate_session", wrapped_validate_session)


def unpatch_pymongo_module():
    if not getattr(pymongo, "_datadog_patch", False):
        return
    pymongo._datadog_patch = False

    _u(pymongo.server.Server, _CHECKOUT_FN_NAME)
    _u(_VERIFY_VERSION_CLASS, "validate_session")


@contextlib.contextmanager
def traced_get_socket(wrapped, instance, args, kwargs):
    pin = Pin._find(wrapped, instance)
    if not pin or not pin.enabled():
        with wrapped(*args, **kwargs) as sock_info:
            yield sock_info
            return

    with pin.tracer.trace(
        "pymongo.%s" % _CHECKOUT_FN_NAME,
        service=trace_utils.int_service(pin, config.pymongo),
        span_type=SpanTypes.MONGODB,
    ) as span:
        span.set_tag_str(COMPONENT, config.pymongo.integration_name)
        span.set_tag_str(db.SYSTEM, mongo.SERVICE)

        # set span.kind tag equal to type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        with wrapped(*args, **kwargs) as sock_info:
            set_address_tags(span, sock_info.address)
            span.set_tag(SPAN_MEASURED_KEY)
            yield sock_info
