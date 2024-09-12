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

# keep TracedMongoClient import to maintain backwards compatibility
from .client import TracedMongoClient  # noqa: F401
from .client import _trace_mongo_client_init
from .client import _trace_server_run_operation_and_with_response
from .client import _trace_server_send_message_with_response
from .client import _trace_socket_command
from .client import _trace_socket_write_command
from .client import _trace_topology_select_server
from .client import set_address_tags


_CHECKOUT_FN_NAME = "get_socket" if pymongo.version_tuple < (4, 5) else "checkout"


config._add(
    "pymongo",
    dict(_default_service="pymongo"),
)


def get_version():
    # type: () -> str
    return getattr(pymongo, "__version__", "")


_VERSION = pymongo.version_tuple


def patch():
    if getattr(pymongo, "_datadog_patch", False):
        return
    patch_pymongo_module()
    pymongo._datadog_patch = True


def unpatch():
    if not getattr(pymongo, "_datadog_patch", False):
        return
    unpatch_pymongo_module()
    pymongo._datadog_patch = False


def patch_pymongo_module():
    _w(pymongo.MongoClient.__init__, _trace_mongo_client_init)
    _w(pymongo.topology.Topology.select_server, _trace_topology_select_server)
    if _VERSION >= (3, 12):
        _w(pymongo.server.Server.run_operation, _trace_server_run_operation_and_with_response)
    elif _VERSION >= (3, 9):
        _w(pymongo.server.Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _w(pymongo.server.Server.send_message_with_response, _trace_server_send_message_with_response)

    if _VERSION >= (4, 5):
        _w(pymongo.server.Server.checkout, traced_get_socket)
        _w(pymongo.pool.Connection.command, _trace_socket_command)
        _w(pymongo.pool.Connection.write_command, _trace_socket_write_command)
    else:
        _w(pymongo.server.Server.get_socket, traced_get_socket)
        _w(pymongo.pool.SocketInfo.command, _trace_socket_command)
        _w(pymongo.pool.SocketInfo.write_command, _trace_socket_write_command)


def unpatch_pymongo_module():
    _u(pymongo.MongoClient.__init__, _trace_mongo_client_init)
    _u(pymongo.topology.Topology.select_server, _trace_topology_select_server)

    if _VERSION >= (3, 12):
        _u(pymongo.server.Server.run_operation, _trace_server_run_operation_and_with_response)
    elif _VERSION >= (3, 9):
        _u(pymongo.server.Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _u(pymongo.server.Server.send_message_with_response, _trace_server_send_message_with_response)

    if _VERSION >= (4, 5):
        _u(pymongo.server.Server.checkout, traced_get_socket)
        _u(pymongo.pool.Connection.command, _trace_socket_command)
        _u(pymongo.pool.Connection.write_command, _trace_socket_write_command)
    else:
        _u(pymongo.server.Server.get_socket, traced_get_socket)
        _u(pymongo.pool.SocketInfo.command, _trace_socket_command)
        _u(pymongo.pool.SocketInfo.write_command, _trace_socket_write_command)


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
            # Ensure the pin used on the traced mongo client is passed down to the socket instance
            # (via the server instance)
            Pin.get_from(instance).onto(sock_info)
            yield sock_info
