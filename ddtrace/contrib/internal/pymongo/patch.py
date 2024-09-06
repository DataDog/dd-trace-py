import contextlib

import pymongo

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w

# keep TracedMongoClient import to maintain bakcwards compatibility
from .client import TracedMongoClient  # noqa: F401
from .client import set_address_tags
from .client import trace_mongo_client_init


config._add(
    "pymongo",
    dict(_default_service="pymongo"),
)


def get_version():
    # type: () -> str
    return getattr(pymongo, "__version__", "")


_VERSION = pymongo.version_tuple
_CHECKOUT_FN_NAME = "get_socket" if _VERSION < (4, 5) else "checkout"


def patch():
    if getattr(pymongo, "_datadog_patch", False):
        return
    patch_pymongo_module()
    _w(pymongo.MongoClient.__init__, trace_mongo_client_init)
    pymongo._datadog_patch = True


def unpatch():
    if not getattr(pymongo, "_datadog_patch", False):
        return
    unpatch_pymongo_module()
    _u(pymongo.MongoClient, pymongo.MongoClient.__init__)
    pymongo._datadog_patch = False


def patch_pymongo_module():
    # Whenever a pymongo command is invoked, the lib either:
    # - Creates a new socket & performs a TCP handshake
    # - Grabs a socket already initialized before
    checkout_fn = getattr(pymongo.server.Server, _CHECKOUT_FN_NAME)
    _w(checkout_fn, traced_get_socket)


def unpatch_pymongo_module():
    checkout_fn = getattr(pymongo.server.Server, _CHECKOUT_FN_NAME)
    _u(checkout_fn, traced_get_socket)


@contextlib.contextmanager
def traced_get_socket(func, args, kwargs):
    instance = get_argument_value(args, kwargs, 0, "self")
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        with func(*args, **kwargs) as sock_info:
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

        with func(*args, **kwargs) as sock_info:
            set_address_tags(span, sock_info.address)
            span.set_tag(SPAN_MEASURED_KEY)
            yield sock_info
