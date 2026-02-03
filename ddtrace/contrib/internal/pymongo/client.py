# stdlib
import contextlib
import functools

# 3p
import pymongo
from wrapt import ObjectProxy

# project
from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo as mongox
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap as _u
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.trace import tracer

from .parse import parse_msg
from .parse import parse_query
from .parse import parse_spec
from .utils import create_checkout_span
from .utils import dbm_dispatch
from .utils import is_query
from .utils import process_server_message_result
from .utils import process_server_operation_result
from .utils import set_address_tags
from .utils import set_query_metadata
from .utils import setup_checkout_span_tags


VERSION = pymongo.version_tuple


if VERSION >= (4, 9):
    from pymongo.synchronous.pool import Connection
    from pymongo.synchronous.server import Server
    from pymongo.synchronous.topology import Topology
elif VERSION >= (4, 5):
    from pymongo.pool import Connection
    from pymongo.server import Server
    from pymongo.topology import Topology
else:
    from pymongo.pool import SocketInfo as Connection
    from pymongo.server import Server
    from pymongo.topology import Topology


log = get_logger(__name__)

_DEFAULT_SERVICE = schematize_service_name("pymongo")


# TODO(mabdinur): Remove TracedMongoClient when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedMongoClient(ObjectProxy):
    pass


_CHECKOUT_FN_NAME = "get_socket" if pymongo.version_tuple < (4, 5) else "checkout"


def patch_pymongo_sync_modules():
    """Patch synchronous pymongo modules."""
    _w(pymongo.MongoClient.__init__, _trace_mongo_client_init)
    _w(Topology.select_server, _trace_topology_select_server)
    if VERSION >= (3, 12):
        _w(Server.run_operation, _trace_server_run_operation_and_with_response)
    elif VERSION >= (3, 9):
        _w(Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _w(Server.send_message_with_response, _trace_server_send_message_with_response)

    if VERSION >= (4, 5):
        _w(Server.checkout, traced_get_socket)
    else:
        _w(Server.get_socket, traced_get_socket)
    _w(Connection.command, _trace_socket_command)
    _w(Connection.write_command, _trace_socket_write_command)


def unpatch_pymongo_sync_modules():
    """Unpatch synchronous pymongo modules."""
    _u(pymongo.MongoClient.__init__, _trace_mongo_client_init)
    _u(Topology.select_server, _trace_topology_select_server)

    if VERSION >= (3, 12):
        _u(Server.run_operation, _trace_server_run_operation_and_with_response)
    elif VERSION >= (3, 9):
        _u(Server.run_operation_with_response, _trace_server_run_operation_and_with_response)
    else:
        _u(Server.send_message_with_response, _trace_server_send_message_with_response)

    if VERSION >= (4, 5):
        _u(Server.checkout, traced_get_socket)
    else:
        _u(Server.get_socket, traced_get_socket)
    _u(Connection.command, _trace_socket_command)
    _u(Connection.write_command, _trace_socket_write_command)


def setup_mongo_client_pin(client):
    """Set up pin handling on mongo client. Shared between sync and async."""

    def __setddpin__(client, pin):
        pin.onto(client._topology)

    def __getddpin__(client):
        return Pin.get_from(client._topology)

    # Set a pin on the mongoclient pin on the topology object
    # This allows us to pass the same pin to the server objects
    client.__setddpin__ = functools.partial(__setddpin__, client)
    client.__getddpin__ = functools.partial(__getddpin__, client)

    # Set a pin on the traced mongo client
    Pin(service=None).onto(client)


def _trace_mongo_client_init(func, args, kwargs):
    func(*args, **kwargs)
    client = get_argument_value(args, kwargs, 0, "self")
    setup_mongo_client_pin(client)


def propagate_pin_to_server(server, topology_instance):
    """Propagate pin from topology to server. Shared between sync and async."""
    pin = Pin.get_from(topology_instance)
    if pin is not None:
        pin.onto(server)


def _trace_topology_select_server(func, args, kwargs):
    server = func(*args, **kwargs)
    # Ensure the pin used on the traced mongo client is passed down to the topology instance
    # This allows us to pass the same pin in traced server objects.
    topology_instance = get_argument_value(args, kwargs, 0, "self")
    propagate_pin_to_server(server, topology_instance)
    return server


# TODO(mabdinur): Remove TracedServer when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedServer(ObjectProxy):
    pass


def datadog_trace_operation(operation, wrapped):
    cmd = None
    # Only try to parse something we think is a query.
    if is_query(operation):
        try:
            cmd = parse_query(operation)
        except Exception:
            log.exception("error parsing query")

    # Gets the pin from the mongo client (through the topology object)
    pin = Pin.get_from(wrapped)
    # if we couldn't parse or shouldn't trace the message, just go.
    if not cmd or not pin or not pin.enabled():
        return None

    span = tracer.trace(
        schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
        span_type=SpanTypes.MONGODB,
        service=trace_utils.ext_service(pin, config.pymongo),
    )

    span._set_tag_str(COMPONENT, config.pymongo.integration_name)

    # set span.kind to the operation type being performed
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)

    # PERF: avoid setting via Span.set_tag
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    span._set_tag_str(mongox.DB, cmd.db)
    span._set_tag_str(mongox.COLLECTION, cmd.coll)
    span._set_tag_str(db.SYSTEM, mongox.SERVICE)
    span.set_tags(cmd.tags)

    # set `mongodb.query` tag and resource for span
    set_query_metadata(span, cmd)

    return span


def _trace_server_run_operation_and_with_response(func, args, kwargs):
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 2, "operation")

    span = datadog_trace_operation(operation, server_instance)
    if span is None:
        return func(*args, **kwargs)
    with span:
        span, args, kwargs = dbm_dispatch(span, args, kwargs)
        result = func(*args, **kwargs)
        return process_server_operation_result(span, operation, result)


def _trace_server_send_message_with_response(func, args, kwargs):
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 1, "operation")

    span = datadog_trace_operation(operation, server_instance)
    if span is None:
        return func(*args, **kwargs)
    with span:
        result = func(*args, **kwargs)
        return process_server_message_result(span, operation, result)


def parse_socket_command_spec(args, kwargs):
    """
    Parse socket command spec.

    Returns:
        tuple: (socket_instance, dbname, cmd, pin) if parsing succeeds and tracing should proceed
        None: if parsing fails or tracing should be skipped
    """
    socket_instance = get_argument_value(args, kwargs, 0, "self")
    dbname = get_argument_value(args, kwargs, 1, "dbname")
    spec = get_argument_value(args, kwargs, 2, "spec")
    cmd = None
    try:
        cmd = parse_spec(spec, dbname)
    except Exception:
        log.exception("error parsing spec. skipping trace")

    pin = Pin.get_from(socket_instance)
    # skip tracing if we don't have a piece of data we need
    if not dbname or not cmd or not pin or not pin.enabled():
        return None

    cmd.db = dbname
    return (socket_instance, dbname, cmd, pin)


def _trace_socket_command(func, args, kwargs):
    parsed = parse_socket_command_spec(args, kwargs)
    if parsed is None:
        return func(*args, **kwargs)

    socket_instance, dbname, cmd, pin = parsed
    with trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        # dispatch DBM
        s, args, kwargs = dbm_dispatch(s, args, kwargs)
        return func(*args, **kwargs)


def parse_socket_write_command_msg(args, kwargs):
    """
    Parse socket write command msg.

    Returns:
        tuple: (socket_instance, cmd, pin) if parsing succeeds and tracing should proceed
        None: if parsing fails or tracing should be skipped
    """
    socket_instance = get_argument_value(args, kwargs, 0, "self")
    msg = get_argument_value(args, kwargs, 2, "msg")
    cmd = None
    try:
        cmd = parse_msg(msg)
    except Exception:
        log.exception("error parsing msg")

    pin = Pin.get_from(socket_instance)
    # if we couldn't parse it, don't try to trace it.
    if not cmd or not pin or not pin.enabled():
        return None

    return (socket_instance, cmd, pin)


def _trace_socket_write_command(func, args, kwargs):
    parsed = parse_socket_write_command_msg(args, kwargs)
    if parsed is None:
        return func(*args, **kwargs)

    socket_instance, cmd, pin = parsed
    with trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        result = func(*args, **kwargs)
        if result:
            s.set_metric(db.ROWCOUNT, result.get("n", -1))
        return result


def trace_cmd(cmd, socket_instance, address):
    pin = Pin.get_from(socket_instance)
    s = tracer.trace(
        schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
        span_type=SpanTypes.MONGODB,
        service=trace_utils.ext_service(pin, config.pymongo),
    )

    s._set_tag_str(COMPONENT, config.pymongo.integration_name)
    s._set_tag_str(db.SYSTEM, mongox.SERVICE)

    # set span.kind to the type of operation being performed
    s._set_tag_str(SPAN_KIND, SpanKind.CLIENT)

    # PERF: avoid setting via Span.set_tag
    s.set_metric(_SPAN_MEASURED_KEY, 1)
    if cmd.db:
        s._set_tag_str(mongox.DB, cmd.db)
    if cmd:
        s.set_tag(mongox.COLLECTION, cmd.coll)
        s.set_tags(cmd.tags)
        s.set_metrics(cmd.metrics)

    # set `mongodb.query` tag and resource for span
    set_query_metadata(s, cmd)

    if address:
        set_address_tags(s, address)
    return s


@contextlib.contextmanager
def traced_get_socket(func, args, kwargs):
    instance = get_argument_value(args, kwargs, 0, "self")
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        with func(*args, **kwargs) as sock_info:
            yield sock_info
            return

    with create_checkout_span(pin, _CHECKOUT_FN_NAME) as span:
        with func(*args, **kwargs) as sock_info:
            setup_checkout_span_tags(span, sock_info, instance)
            yield sock_info
