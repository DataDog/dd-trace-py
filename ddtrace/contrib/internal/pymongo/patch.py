import contextlib
from typing import Dict

import pymongo

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.trace import Pin
from ddtrace.vendor.sqlcommenter import _generate_comment_from_metadata as _generate_comment_from_metadata

from ....internal.schema import schematize_service_name

# keep TracedMongoClient import to maintain backwards compatibility
from .client import TracedMongoClient  # noqa: F401
from .client import _dbm_comment_injector
from .client import _trace_mongo_client_init
from .client import _trace_server_run_operation_and_with_response
from .client import _trace_server_send_message_with_response
from .client import _trace_socket_command
from .client import _trace_socket_write_command
from .client import _trace_topology_select_server
from .client import set_address_tags


_VERSION = pymongo.version_tuple

if _VERSION >= (4, 9):
    from pymongo.synchronous.pool import Connection
    from pymongo.synchronous.server import Server
    from pymongo.synchronous.topology import Topology
elif _VERSION >= (4, 5):
    from pymongo.pool import Connection
    from pymongo.server import Server
    from pymongo.topology import Topology
else:
    from pymongo.pool import SocketInfo as Connection
    from pymongo.server import Server
    from pymongo.topology import Topology


_CHECKOUT_FN_NAME = "get_socket" if pymongo.version_tuple < (4, 5) else "checkout"

log = get_logger(__name__)


config._add(
    "pymongo",
    dict(
        _default_service=schematize_service_name("pymongo"),
        _dbm_propagator=_DBM_Propagator(2, "spec", _dbm_comment_injector, _generate_comment_from_metadata),
    ),
)


def get_version():
    # type: () -> str
    return getattr(pymongo, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"pymongo": ">=3.8.0"}


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
    _w(Topology.select_server, _trace_topology_select_server)
    if _VERSION >= (3, 12):
        _w(Server.run_operation, _trace_server_run_operation_and_with_response)
    elif _VERSION >= (3, 9):
        _w(Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _w(Server.send_message_with_response, _trace_server_send_message_with_response)

    if _VERSION >= (4, 5):
        _w(Server.checkout, traced_get_socket)
    else:
        _w(Server.get_socket, traced_get_socket)
    _w(Connection.command, _trace_socket_command)
    _w(Connection.write_command, _trace_socket_write_command)


def unpatch_pymongo_module():
    _u(pymongo.MongoClient.__init__, _trace_mongo_client_init)
    _u(Topology.select_server, _trace_topology_select_server)

    if _VERSION >= (3, 12):
        _u(Server.run_operation, _trace_server_run_operation_and_with_response)
    elif _VERSION >= (3, 9):
        _u(Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _u(Server.send_message_with_response, _trace_server_send_message_with_response)

    if _VERSION >= (4, 5):
        _u(Server.checkout, traced_get_socket)
    else:
        _u(Server.get_socket, traced_get_socket)
    _u(Connection.command, _trace_socket_command)
    _u(Connection.write_command, _trace_socket_write_command)


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
        service=trace_utils.ext_service(pin, config.pymongo),
        span_type=SpanTypes.MONGODB,
    ) as span:
        span.set_tag_str(COMPONENT, config.pymongo.integration_name)
        span.set_tag_str(db.SYSTEM, mongo.SERVICE)

        # set span.kind tag equal to type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        with func(*args, **kwargs) as sock_info:
            set_address_tags(span, sock_info.address)
            span.set_tag(_SPAN_MEASURED_KEY)
            # Ensure the pin used on the traced mongo client is passed down to the socket instance
            # (via the server instance)
            Pin.get_from(instance).onto(sock_info)
            yield sock_info
