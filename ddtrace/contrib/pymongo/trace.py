
# stdlib
import contextlib
import logging

# 3p
from wrapt import ObjectProxy

# project
from ...compat import iteritems, json
from ...ext import AppTypes
from ...ext import mongo as mongox
from ...ext import net as netx
from .parse import parse_spec, parse_query, parse_msg


log = logging.getLogger(__name__)


def trace_mongo_client(client, tracer, service=mongox.TYPE):
    tracer.set_service_info(
        service=service,
        app=mongox.TYPE,
        app_type=AppTypes.db,
    )
    return TracedMongoClient(tracer, service, client)


class TracedSocket(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, sock):
        super(TracedSocket, self).__init__(sock)
        self._tracer = tracer
        self._srv = service

    def command(self, dbname, spec, *args, **kwargs):
        cmd = None
        try:
            cmd = parse_spec(spec, dbname)
        except Exception:
            log.exception("error parsing spec. skipping trace")

        # skip tracing if we don't have a piece of data we need
        if not dbname or not cmd:
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

        # if we couldn't parse it, don't try to trace it.
        if not cmd:
            return self.__wrapped__.write_command(request_id, msg)

        with self.__trace(cmd) as s:
            s.resource = _resource_from_cmd(cmd)
            result = self.__wrapped__.write_command(request_id, msg)
            if result:
                s.set_metric(mongox.ROWS, result.get("n", -1))
            return result

    def __trace(self, cmd):
        s = self._tracer.trace(
            "pymongo.cmd",
            span_type=mongox.TYPE,
            service=self._srv)

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


class TracedServer(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, topology):
        super(TracedServer, self).__init__(topology)
        self._tracer = tracer
        self._srv = service

    def send_message_with_response(self, operation, *args, **kwargs):
        cmd = None
        # Only try to parse something we think is a query.
        if self._is_query(operation):
            try:
                cmd = parse_query(operation)
            except Exception:
                log.exception("error parsing query")

        # if we couldn't parse or shouldn't trace the message, just go.
        if not cmd:
            return self.__wrapped__.send_message_with_response(
                operation,
                *args,
                **kwargs)

        with self._tracer.trace(
                "pymongo.cmd",
                span_type=mongox.TYPE,
                service=self._srv) as span:

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
            if isinstance(s, TracedSocket):
                yield s
            else:
                yield TracedSocket(self._tracer, self._srv, s)

    @staticmethod
    def _is_query(op):
        # NOTE: _Query should alwyas have a spec field
        return hasattr(op, 'spec')


class TracedTopology(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, topology):
        super(TracedTopology, self).__init__(topology)
        self._tracer = tracer
        self._srv = service

    def select_server(self, *args, **kwargs):
        s = self.__wrapped__.select_server(*args, **kwargs)
        if isinstance(s, TracedServer):
            return s
        else:
            return TracedServer(self._tracer, self._srv, s)


class TracedMongoClient(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, client):
        # NOTE[matt] the TracedMongoClient attempts to trace all of the network
        # calls in the trace library. This is good because it measures the
        # actual network time. It's bad because it uses a private API which
        # could change. We'll see how this goes.
        client._topology = TracedTopology(tracer, service, client._topology)
        super(TracedMongoClient, self).__init__(client)
        self._tracer = tracer
        self._srv = service


def normalize_filter(f=None):
    if f is None:
        return {}
    elif isinstance(f, list):
        # normalize lists of filters
        # e.g. {$or: [ { age: { $lt: 30 } }, { type: 1 } ]}
        return [normalize_filter(s) for s in f]
    else:
        # normalize dicts of filters
        # e.g. {$or: [ { age: { $lt: 30 } }, { type: 1 } ]})
        out = {}
        for k, v in iteritems(f):
            if isinstance(v, list) or isinstance(v, dict):
                # RECURSION ALERT: needs to move to the agent
                out[k] = normalize_filter(v)
            else:
                out[k] = '?'
        return out

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
