# stdlib
import contextlib
import json
from typing import Iterable

# 3p
import pymongo

# project
import ddtrace
from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import ObjectProxy

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...ext import db
from ...ext import mongo as mongox
from ...ext import net as netx
from ...internal.logger import get_logger
from ...internal.schema import schematize_database_operation
from ...internal.schema import schematize_service_name
from ...internal.utils import get_argument_value
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


class TracedMongoClient(ObjectProxy):
    def __init__(self, client=None, *args, **kwargs):
        # To support the former trace_mongo_client interface, we have to keep this old interface
        # TODO(Benjamin): drop it in a later version
        if not isinstance(client, _MongoClient):
            # Patched interface, instantiate the client

            # client is just the first arg which could be the host if it is
            # None, then it could be that the caller:

            # if client is None then __init__ was:
            #   1) invoked with host=None
            #   2) not given a first argument (client defaults to None)
            # we cannot tell which case it is, but it should not matter since
            # the default value for host is None, in either case we can simply
            # not provide it as an argument
            if client is None:
                client = _MongoClient(*args, **kwargs)
            # else client is a value for host so just pass it along
            else:
                client = _MongoClient(client, *args, **kwargs)

        super(TracedMongoClient, self).__init__(client)
        client._datadog_proxy = self
        # NOTE[matt] the TracedMongoClient attempts to trace all of the network
        # calls in the trace library. This is good because it measures the
        # actual network time. It's bad because it uses a private API which
        # could change. We'll see how this goes.
        if not isinstance(client._topology, TracedTopology):
            client._topology = TracedTopology(client._topology)

        # Default Pin
        ddtrace.Pin(service=_DEFAULT_SERVICE).onto(self)

    def __setddpin__(self, pin):
        pin.onto(self._topology)

    def __getddpin__(self):
        return ddtrace.Pin.get_from(self._topology)


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


class TracedTopology(ObjectProxy):
    def __init__(self, topology):
        super(TracedTopology, self).__init__(topology)

    def select_server(self, *args, **kwargs):
        s = self.__wrapped__.select_server(*args, **kwargs)
        if not isinstance(s, TracedServer):
            s = TracedServer(s)
        # Reattach the pin every time in case it changed since the initial patching
        ddtrace.Pin.get_from(self).onto(s)
        return s


class TracedServer(ObjectProxy):
    def __init__(self, server):
        super(TracedServer, self).__init__(server)

    def _datadog_trace_operation(self, operation):
        cmd = None
        # Only try to parse something we think is a query.
        if self._is_query(operation):
            try:
                cmd = parse_query(operation)
            except Exception:
                log.exception("error parsing query")

        pin = ddtrace.Pin.get_from(self)
        # if we couldn't parse or shouldn't trace the message, just go.
        if not cmd or not pin or not pin.enabled():
            return None

        span = pin.tracer.trace(
            schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
            span_type=SpanTypes.MONGODB,
            service=pin.service,
        )

        span.set_tag_str(COMPONENT, config.pymongo.integration_name)

        # set span.kind to the operation type being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag_str(mongox.DB, cmd.db)
        span.set_tag_str(mongox.COLLECTION, cmd.coll)
        span.set_tag_str(db.SYSTEM, mongox.SERVICE)
        span.set_tags(cmd.tags)

        # set `mongodb.query` tag and resource for span
        _set_query_metadata(span, cmd)

        # set analytics sample rate
        sample_rate = config.pymongo.get_analytics_sample_rate()
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)
        return span

    if VERSION >= (4, 5, 0):

        @contextlib.contextmanager
        def checkout(self, *args, **kwargs):
            with self.__wrapped__.checkout(*args, **kwargs) as s:
                if not isinstance(s, TracedSocket):
                    s = TracedSocket(s)
                ddtrace.Pin.get_from(self).onto(s)
                yield s

    else:

        @contextlib.contextmanager
        def get_socket(self, *args, **kwargs):
            with self.__wrapped__.get_socket(*args, **kwargs) as s:
                if not isinstance(s, TracedSocket):
                    s = TracedSocket(s)
                ddtrace.Pin.get_from(self).onto(s)
                yield s

    if VERSION >= (3, 12, 0):

        def run_operation(self, sock_info, operation, *args, **kwargs):
            span = self._datadog_trace_operation(operation)
            if span is None:
                return self.__wrapped__.run_operation(sock_info, operation, *args, **kwargs)
            with span:
                result = self.__wrapped__.run_operation(sock_info, operation, *args, **kwargs)
                if result:
                    if hasattr(result, "address"):
                        set_address_tags(span, result.address)
                    if self._is_query(operation) and hasattr(result, "docs"):
                        set_query_rowcount(docs=result.docs, span=span)
                return result

    elif (3, 9, 0) <= VERSION < (3, 12, 0):

        def run_operation_with_response(self, sock_info, operation, *args, **kwargs):
            span = self._datadog_trace_operation(operation)
            if span is None:
                return self.__wrapped__.run_operation_with_response(sock_info, operation, *args, **kwargs)
            with span:
                result = self.__wrapped__.run_operation_with_response(sock_info, operation, *args, **kwargs)
                if result:
                    if hasattr(result, "address"):
                        set_address_tags(span, result.address)
                    if self._is_query(operation) and hasattr(result, "docs"):
                        set_query_rowcount(docs=result.docs, span=span)
                return result

    else:

        def send_message_with_response(self, operation, *args, **kwargs):
            span = self._datadog_trace_operation(operation)
            if span is None:
                return self.__wrapped__.send_message_with_response(operation, *args, **kwargs)
            with span:
                result = self.__wrapped__.send_message_with_response(operation, *args, **kwargs)
                if result:
                    if hasattr(result, "address"):
                        set_address_tags(span, result.address)
                    if self._is_query(operation):
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

    @staticmethod
    def _is_query(op):
        # NOTE: _Query should always have a spec field
        return hasattr(op, "spec")


class TracedSocket(ObjectProxy):
    def __init__(self, socket):
        super(TracedSocket, self).__init__(socket)

    def command(self, dbname, spec, *args, **kwargs):
        cmd = None
        try:
            cmd = parse_spec(spec, dbname)
        except Exception:
            log.exception("error parsing spec. skipping trace")

        pin = ddtrace.Pin.get_from(self)
        # skip tracing if we don't have a piece of data we need
        if not dbname or not cmd or not pin or not pin.enabled():
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

        cmd.db = dbname
        with self.__trace(cmd):
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

    def write_command(self, *args, **kwargs):
        msg = get_argument_value(args, kwargs, 1, "msg")
        cmd = None
        try:
            cmd = parse_msg(msg)
        except Exception:
            log.exception("error parsing msg")

        pin = ddtrace.Pin.get_from(self)
        # if we couldn't parse it, don't try to trace it.
        if not cmd or not pin or not pin.enabled():
            return self.__wrapped__.write_command(*args, **kwargs)

        with self.__trace(cmd) as s:
            result = self.__wrapped__.write_command(*args, **kwargs)
            if result:
                s.set_metric(db.ROWCOUNT, result.get("n", -1))
            return result

    def __trace(self, cmd):
        pin = ddtrace.Pin.get_from(self)
        s = pin.tracer.trace(
            schematize_database_operation("pymongo.cmd", database_provider="mongodb"),
            span_type=SpanTypes.MONGODB,
            service=pin.service,
        )

        s.set_tag_str(COMPONENT, config.pymongo.integration_name)
        s.set_tag_str(db.SYSTEM, mongox.SERVICE)

        # set span.kind to the type of operation being performed
        s.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        s.set_tag(SPAN_MEASURED_KEY)
        if cmd.db:
            s.set_tag_str(mongox.DB, cmd.db)
        if cmd:
            s.set_tag(mongox.COLLECTION, cmd.coll)
            s.set_tags(cmd.tags)
            s.set_metrics(cmd.metrics)

        # set `mongodb.query` tag and resource for span
        _set_query_metadata(s, cmd)

        # set analytics sample rate
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.pymongo.get_analytics_sample_rate())

        if self.address:
            set_address_tags(s, self.address)
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
        span.set_tag(netx.TARGET_PORT, address[1])


def _set_query_metadata(span, cmd):
    """Sets span `mongodb.query` tag and resource given command query"""
    if cmd.query:
        nq = normalize_filter(cmd.query)
        span.set_tag("mongodb.query", nq)
        # needed to dump json so we don't get unicode
        # dict keys like {u'foo':'bar'}
        q = json.dumps(nq)
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
