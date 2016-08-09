
# stdlib
import contextlib
import logging

# 3p
from wrapt import ObjectProxy

# project
from ...compat import iteritems
from ...ext import AppTypes
from ...ext import mongo as mongox
from ...ext import net as netx
from .parse import parse_spec, parse_query, Command


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
            cmd = parse_spec(spec)
        except Exception:
            log.exception("error parsing spec. skipping trace")

        # skip tracing if we don't have a piece of data we need
        if not dbname or not cmd:
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

        with self.__trace(dbname, cmd):
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

    def write_command(self, *args, **kwargs):
        # FIXME[matt] parse the db name and collection from the
        # message.
        coll = ""
        db = ""
        cmd = Command("insert_many", coll)
        with self.__trace(db, cmd) as s:
            s.resource = "insert_many"
            result = self.__wrapped__.write_command(*args, **kwargs)
            if result:
                s.set_metric(mongox.ROWS, result.get("n", -1))
            return result

    def __trace(self, db, cmd):
        s = self._tracer.trace(
            "pymongo.cmd",
            span_type=mongox.TYPE,
            service=self._srv,
        )

        if db: s.set_tag(mongox.DB, db)
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

        # if we're processing something unexpected, just skip tracing.
        if getattr(operation, 'name', None) != 'find':
            return self.__wrapped__.send_message_with_response(
                operation,
                *args,
                **kwargs)

        # trace the given query.
        cmd = parse_query(operation)
        with self._tracer.trace(
                "pymongo.cmd",
                span_type=mongox.TYPE,
                service=self._srv) as span:

            span.resource = _resource_from_cmd(cmd)
            span.set_tag(mongox.DB, operation.db)
            span.set_tag(mongox.COLLECTION, cmd.coll)
            span.set_tags(cmd.tags)

            result = self.__wrapped__.send_message_with_response(
                operation,
                *args,
                **kwargs
            )

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
        return "%s %s %s" % (cmd.name, cmd.coll, nq)
    else:
        return "%s %s" % (cmd.name, cmd.coll)
