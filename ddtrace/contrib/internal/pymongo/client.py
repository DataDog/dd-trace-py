# stdlib
import contextlib
import functools
import json
from typing import Iterable

# 3p
import pymongo
from pymongo.message import _GetMore
from pymongo.message import _Query
from wrapt import ObjectProxy

# project
import ddtrace
from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import mongo as mongox
from ddtrace.ext import net as netx
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import Pin

from .parse import parse_msg
from .parse import parse_query
from .parse import parse_spec


BATCH_PARTIAL_KEY = "Batch"

# Original Client class
_MongoClient = pymongo.MongoClient

VERSION = pymongo.version_tuple

if VERSION < (3, 6, 0):
    from pymongo.helpers import _unpack_response


log = get_logger(__name__)

_DEFAULT_SERVICE = schematize_service_name("pymongo")


# TODO(mabdinur): Remove TracedMongoClient when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedMongoClient(ObjectProxy):
    pass


def _trace_mongo_client_init(func, args, kwargs):
    func(*args, **kwargs)
    client = get_argument_value(args, kwargs, 0, "self")

    def __setddpin__(client, pin):
        pin.onto(client._topology)

    def __getddpin__(client):
        return ddtrace.trace.Pin.get_from(client._topology)

    # Set a pin on the mongoclient pin on the topology object
    # This allows us to pass the same pin to the server objects
    client.__setddpin__ = functools.partial(__setddpin__, client)
    client.__getddpin__ = functools.partial(__getddpin__, client)

    # Set a pin on the traced mongo client
    Pin(service=None).onto(client)


# The function is exposed in the public API, but it is not used in the codebase.
# TODO(mabdinur): Remove this function when ddtrace.contrib.pymongo.client is removed.
@contextlib.contextmanager
def wrapped_validate_session(wrapped, instance, args, kwargs):
    # We do this to handle a validation `A is B` in pymongo that
    # relies on IDs being equal. Since we are proxying objects, we need
    # to ensure we're compare proxy with proxy or wrapped with wrapped
    # or this validation will fail
    client = args[0]
    session = args[1]
    session_client = session._client
    if isinstance(session_client, TracedMongoClient):
        if isinstance(client, _MongoClient):
            client = getattr(client, "_datadog_proxy", client)
    elif isinstance(session_client, _MongoClient):
        if isinstance(client, TracedMongoClient):
            client = client.__wrapped__

    yield wrapped(client, session)


# TODO(mabdinur): Remove TracedTopology when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedTopology(ObjectProxy):
    pass


def _trace_topology_select_server(func, args, kwargs):
    server = func(*args, **kwargs)
    # Ensure the pin used on the traced mongo client is passed down to the topology instance
    # This allows us to pass the same pin in traced server objects.
    topology_instance = get_argument_value(args, kwargs, 0, "self")
    pin = ddtrace.trace.Pin.get_from(topology_instance)

    if pin is not None:
        pin.onto(server)
    return server


# TODO(mabdinur): Remove TracedServer when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedServer(ObjectProxy):
    pass


def _datadog_trace_operation(operation, wrapped):
    cmd = None
    # Only try to parse something we think is a query.
    if _is_query(operation):
        try:
            cmd = parse_query(operation)
        except Exception:
            log.exception("error parsing query")

    # Gets the pin from the mogno client (through the topology object)
    pin = ddtrace.trace.Pin.get_from(wrapped)
    # if we couldn't parse or shouldn't trace the message, just go.
    if not cmd or not pin or not pin.enabled():
        return None

    span = pin.tracer.trace(
        schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
        span_type=SpanTypes.MONGODB,
        service=trace_utils.ext_service(pin, config.pymongo),
    )

    span.set_tag_str(COMPONENT, config.pymongo.integration_name)

    # set span.kind to the operation type being performed
    span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

    span.set_tag(_SPAN_MEASURED_KEY)
    span.set_tag_str(mongox.DB, cmd.db)
    span.set_tag_str(mongox.COLLECTION, cmd.coll)
    span.set_tag_str(db.SYSTEM, mongox.SERVICE)
    span.set_tags(cmd.tags)

    # set `mongodb.query` tag and resource for span
    _set_query_metadata(span, cmd)

    return span


def _trace_server_run_operation_and_with_response(func, args, kwargs):
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 2, "operation")

    span = _datadog_trace_operation(operation, server_instance)
    if span is None:
        return func(*args, **kwargs)
    with span:
        span, args, kwargs = _dbm_dispatch(span, args, kwargs)
        result = func(*args, **kwargs)
        if result:
            if hasattr(result, "address"):
                set_address_tags(span, result.address)
            if _is_query(operation) and hasattr(result, "docs"):
                set_query_rowcount(docs=result.docs, span=span)
        return result


def _trace_server_send_message_with_response(func, args, kwargs):
    server_instance = get_argument_value(args, kwargs, 0, "self")
    operation = get_argument_value(args, kwargs, 1, "operation")

    span = _datadog_trace_operation(operation, server_instance)
    if span is None:
        return func(*args, **kwargs)
    with span:
        result = func(*args, **kwargs)
        if result:
            if hasattr(result, "address"):
                set_address_tags(span, result.address)
            if _is_query(operation):
                if hasattr(result, "data"):
                    if VERSION >= (3, 6, 0) and hasattr(result.data, "unpack_response"):
                        set_query_rowcount(docs=result.data.unpack_response(), span=span)
                    else:
                        data = _unpack_response(response=result.data)
                        if VERSION < (3, 2, 0) and data.get("number_returned", None):
                            span.set_metric(db.ROWCOUNT, data.get("number_returned"))
                        elif (3, 2, 0) <= VERSION < (3, 6, 0):
                            docs = data.get("data", None)
                            set_query_rowcount(docs=docs, span=span)
        return result


def _is_query(op):
    # NOTE: _Query should always have a spec field
    return hasattr(op, "spec")


# TODO(mabdinur): Remove TracedSocket when ddtrace.contrib.pymongo.client is removed from the public API.
class TracedSocket(ObjectProxy):
    pass


def _trace_socket_command(func, args, kwargs):
    socket_instance = get_argument_value(args, kwargs, 0, "self")
    dbname = get_argument_value(args, kwargs, 1, "dbname")
    spec = get_argument_value(args, kwargs, 2, "spec")
    cmd = None
    try:
        cmd = parse_spec(spec, dbname)
    except Exception:
        log.exception("error parsing spec. skipping trace")

    pin = ddtrace.trace.Pin.get_from(socket_instance)
    # skip tracing if we don't have a piece of data we need
    if not dbname or not cmd or not pin or not pin.enabled():
        return func(*args, **kwargs)

    cmd.db = dbname
    with _trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        # dispatch DBM
        s, args, kwargs = _dbm_dispatch(s, args, kwargs)
        return func(*args, **kwargs)


def _trace_socket_write_command(func, args, kwargs):
    socket_instance = get_argument_value(args, kwargs, 0, "self")
    msg = get_argument_value(args, kwargs, 2, "msg")
    cmd = None
    try:
        cmd = parse_msg(msg)
    except Exception:
        log.exception("error parsing msg")

    pin = ddtrace.trace.Pin.get_from(socket_instance)
    # if we couldn't parse it, don't try to trace it.
    if not cmd or not pin or not pin.enabled():
        return func(*args, **kwargs)

    with _trace_cmd(cmd, socket_instance, socket_instance.address) as s:
        result = func(*args, **kwargs)
        if result:
            s.set_metric(db.ROWCOUNT, result.get("n", -1))
        return result


def _trace_cmd(cmd, socket_instance, address):
    pin = ddtrace.trace.Pin.get_from(socket_instance)
    s = pin.tracer.trace(
        schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
        span_type=SpanTypes.MONGODB,
        service=trace_utils.ext_service(pin, config.pymongo),
    )

    s.set_tag_str(COMPONENT, config.pymongo.integration_name)
    s.set_tag_str(db.SYSTEM, mongox.SERVICE)

    # set span.kind to the type of operation being performed
    s.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

    s.set_tag(_SPAN_MEASURED_KEY)
    if cmd.db:
        s.set_tag_str(mongox.DB, cmd.db)
    if cmd:
        s.set_tag(mongox.COLLECTION, cmd.coll)
        s.set_tags(cmd.tags)
        s.set_metrics(cmd.metrics)

    # set `mongodb.query` tag and resource for span
    _set_query_metadata(s, cmd)

    if address:
        set_address_tags(s, address)
    return s


def normalize_filter(f=None):
    if f is None:
        return {}
    elif isinstance(f, list):
        # normalize lists of filters
        # e.g. {$or: [ { age: { $lt: 30 } }, { type: 1 } ]}
        return [normalize_filter(s) for s in f]
    elif isinstance(f, dict):
        # normalize dicts of filters
        #   {$or: [ { age: { $lt: 30 } }, { type: 1 } ]})
        out = {}
        for k, v in f.items():
            if k == "$in" or k == "$nin":
                # special case $in queries so we don't loop over lists.
                out[k] = "?"
            elif isinstance(v, list) or isinstance(v, dict):
                # RECURSION ALERT: needs to move to the agent
                out[k] = normalize_filter(v)
            else:
                # NOTE: this shouldn't happen, but let's have a safeguard.
                out[k] = "?"
        return out
    else:
        # FIXME[matt] unexpected type. not sure this should ever happen, but at
        # least it won't crash.
        return {}


def set_address_tags(span, address):
    # the address is only set after the cursor is done.
    if address:
        span.set_tag_str(netx.TARGET_HOST, address[0])
        span.set_tag_str(netx.SERVER_ADDRESS, address[0])
        span.set_tag(netx.TARGET_PORT, address[1])


def _set_query_metadata(span, cmd):
    """Sets span `mongodb.query` tag and resource given command query"""
    if cmd.query:
        nq = normalize_filter(cmd.query)
        # needed to dump json so we don't get unicode
        # dict keys like {u'foo':'bar'}
        q = json.dumps(nq)
        span.set_tag("mongodb.query", q)
        span.resource = "{} {} {}".format(cmd.name, cmd.coll, q)
    else:
        span.resource = "{} {}".format(cmd.name, cmd.coll)


def set_query_rowcount(docs, span):
    # results returned in batches, get len of each batch
    if isinstance(docs, Iterable) and len(docs) > 0:
        cursor = docs[0].get("cursor", None)
    if cursor:
        rowcount = sum([len(documents) for batch_key, documents in cursor.items() if BATCH_PARTIAL_KEY in batch_key])
        span.set_metric(db.ROWCOUNT, rowcount)


def _dbm_dispatch(span, args, kwargs):
    # dispatch DBM
    result = core.dispatch_with_results("pymongo.execute", (config.pymongo, span, args, kwargs)).result
    if result:
        span, args, kwargs = result.value
    return span, args, kwargs


def _dbm_comment_injector(dbm_comment, command):
    try:
        if VERSION < (3, 9):
            log.debug("DBM propagation not supported for PyMongo versions < 3.9")
            return command

        if dbm_comment is not None:
            dbm_comment = dbm_comment.strip()
        if isinstance(command, _Query):
            if _is_query(command):
                if "$query" not in command.spec:
                    command.spec = {"$query": command.spec}
                command.spec = _dbm_comment_injector(dbm_comment, command.spec)
        elif isinstance(command, _GetMore):
            if hasattr(command, "comment"):
                command.comment = _dbm_merge_comment(command.comment, dbm_comment)
        else:
            comment_exists = False
            for comment_key in ("comment", "$comment"):
                if comment_key in command:
                    command[comment_key] = _dbm_merge_comment(command[comment_key], dbm_comment)
                    comment_exists = True
            if not comment_exists:
                command["comment"] = dbm_comment
        return command
    except (TypeError, ValueError):
        log.warning(
            "Linking Database Monitoring profiles to spans is not supported for the following query type: %s. "
            "To disable this feature please set the following environment variable: "
            "DD_DBM_PROPAGATION_MODE=disabled",
            type(command),
        )
    return command


def _dbm_merge_comment(existing_comment, dbm_comment):
    if existing_comment is None:
        return dbm_comment
    if isinstance(existing_comment, str):
        return existing_comment + "," + dbm_comment
    elif isinstance(existing_comment, list):
        existing_comment.append(dbm_comment)
        return existing_comment
    # if existing_comment is not a string or list
    # do not inject dbm comment
    return existing_comment
