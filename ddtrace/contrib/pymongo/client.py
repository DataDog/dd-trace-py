# stdlib
import contextlib
import logging
import json

# 3p
import pymongo
from wrapt import ObjectProxy

# project
import ddtrace
from ...compat import iteritems
from ...ext import AppTypes
from ...ext import mongo as mongox
from ...ext import net as netx
from ...util import deprecated
from .parse import parse_spec, parse_query, parse_msg

# Original Client class
_MongoClient = pymongo.MongoClient

log = logging.getLogger(__name__)


@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def trace_mongo_client(client, tracer, service=mongox.TYPE):
    tracer.set_service_info(
        service=service,
        app=mongox.TYPE,
        app_type=AppTypes.db,
    )
    traced_client = TracedMongoClient(client)
    ddtrace.Pin(service=service, tracer=tracer).onto(traced_client)
    return traced_client


class TracedMongoClient(ObjectProxy):

    def __init__(self, client=None, *args, **kwargs):
        # To support the former trace_mongo_client interface, we have to keep this old interface
        # TODO(Benjamin): drop it in a later version
        if not isinstance(client, _MongoClient):
            # Patched interface, instanciate the client
            # Note that, in that case, the client argument isn't a client, it's just the first arg
            client = _MongoClient(client, *args, **kwargs)

        super(TracedMongoClient, self).__init__(client)
        # NOTE[matt] the TracedMongoClient attempts to trace all of the network
        # calls in the trace library. This is good because it measures the
        # actual network time. It's bad because it uses a private API which
        # could change. We'll see how this goes.
        client._topology = TracedTopology(client._topology)

        # Default Pin
        ddtrace.Pin(service=mongox.TYPE, app=mongox.TYPE, app_type=AppTypes.db).onto(self)

    def __setddpin__(self, pin):
        pin.onto(self._topology)

    def __getddpin__(self):
        return ddtrace.Pin.get_from(self._topology)


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

    def send_message_with_response(self, operation, *args, **kwargs):
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
            return self.__wrapped__.send_message_with_response(
                operation,
                *args,
                **kwargs)

        with pin.tracer.trace(
                "pymongo.cmd",
                span_type=mongox.TYPE,
                service=pin.service) as span:

            span.resource = _resource_from_cmd(cmd)
            span.set_tag(mongox.DB, cmd.db)
            span.set_tag(mongox.COLLECTION, cmd.coll)
            span.set_tags(cmd.tags)

            result = self.__wrapped__.send_message_with_response(
                operation,
                *args,
                **kwargs)

            if result and result.address:
                _set_address_tags(span, result.address)
            return result

    @contextlib.contextmanager
    def get_socket(self, *args, **kwargs):
        with self.__wrapped__.get_socket(*args, **kwargs) as s:
            if not isinstance(s, TracedSocket):
                s = TracedSocket(s)
            ddtrace.Pin.get_from(self).onto(s)
            yield s

    @staticmethod
    def _is_query(op):
        # NOTE: _Query should alwyas have a spec field
        return hasattr(op, 'spec')


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

    def write_command(self, request_id, msg):
        cmd = None
        try:
            cmd = parse_msg(msg)
        except Exception:
            log.exception("error parsing msg")

        pin = ddtrace.Pin.get_from(self)
        # if we couldn't parse it, don't try to trace it.
        if not cmd or not pin or not pin.enabled():
            return self.__wrapped__.write_command(request_id, msg)

        with self.__trace(cmd) as s:
            s.resource = _resource_from_cmd(cmd)
            result = self.__wrapped__.write_command(request_id, msg)
            if result:
                s.set_metric(mongox.ROWS, result.get("n", -1))
            return result

    def __trace(self, cmd):
        pin = ddtrace.Pin.get_from(self)
        s = pin.tracer.trace(
            "pymongo.cmd",
            span_type=mongox.TYPE,
            service=pin.service)

        if cmd.db:
            s.set_tag(mongox.DB, cmd.db)
        if cmd:
            s.set_tag(mongox.COLLECTION, cmd.coll)
            s.set_tags(cmd.tags)
            s.set_metrics(cmd.metrics)

        s.resource = _resource_from_cmd(cmd)
        if self.address:
            _set_address_tags(s, self.address)
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
        for k, v in iteritems(f):
            if k == "$in" or k == "$nin":
                # special case $in queries so we don't loop over lists.
                out[k] = "?"
            elif isinstance(v, list) or isinstance(v, dict):
                # RECURSION ALERT: needs to move to the agent
                out[k] = normalize_filter(v)
            else:
                # NOTE: this shouldn't happen, but let's have a safeguard.
                out[k] = '?'
        return out
    else:
        # FIXME[matt] unexpected type. not sure this should ever happen, but at
        # least it won't crash.
        return {}

def _set_address_tags(span, address):
    # the address is only set after the cursor is done.
    if address:
        span.set_tag(netx.TARGET_HOST, address[0])
        span.set_tag(netx.TARGET_PORT, address[1])

def _resource_from_cmd(cmd):
    if cmd.query is not None:
        nq = normalize_filter(cmd.query)
        # needed to dump json so we don't get unicode
        # dict keys like {u'foo':'bar'}
        q = json.dumps(nq)
        return "%s %s %s" % (cmd.name, cmd.coll, q)
    else:
        return "%s %s" % (cmd.name, cmd.coll)
