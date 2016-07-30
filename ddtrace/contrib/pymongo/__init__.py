import contextlib
# 3p
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from wrapt import ObjectProxy

# project
from ...ext import AppTypes
from ...ext import mongo as mongox
from ...ext import net as netx
from  .parse import parse_spec, parse_query


def trace_mongo_client(client, tracer, service="mongodb"):
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
        if not dbname or not spec:
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

        # traced command
        cmd = parse_spec(spec)
        with self.__trace(dbname, cmd) as span:
            return self.__wrapped__.command(dbname, spec, *args, **kwargs)

    def write_command(self, *args, **kwargs):
        with self._tracer.trace("pymongo.cmd", service=self._srv, span_type=mongox.TYPE) as s:
            # FIXME[matt] pluck the collection from the msg.
            s.resource = "insert_many"
            result = self.__wrapped__.write_command(*args, **kwargs)
            if self.address:
                s.set_tag(netx.TARGET_HOST, self.address[0])
                s.set_tag(netx.TARGET_PORT, self.address[1])
            if not result:
                return result
            s.set_metric(mongox.ROWS, result.get("n", -1))
            return result

    def __trace(self, db, cmd):
        s = self._tracer.trace("pymongo.cmd", span_type=mongox.TYPE, service=self._srv)
        if db:
            s.set_tag(mongox.DB, db)
        if cmd:
            s.set_tag(mongox.COLLECTION, cmd.coll)
            s.set_tags(cmd.tags)
            # s.set_metrics(cmd.metrics) FIXME[matt] uncomment whe rebase

        s.resource = _resource_from_cmd(cmd)

        if self.address:
            s.set_tag(netx.TARGET_HOST, self.address[0])
            s.set_tag(netx.TARGET_PORT, self.address[1])
        return s


class TracedServer(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, topology):
        super(TracedServer, self).__init__(topology)
        self._tracer = tracer
        self._srv = service

    def send_message_with_response(self, operation, *args, **kwargs):
        if getattr(operation, 'name', None) != 'find':
            return self.__wrapped__.send_message_with_response(operation, *args, **kwargs)


        cmd = parse_query(operation)
        with self._tracer.trace(
                "pymongo.cmd",
                span_type=mongox.TYPE,
                service=self._srv) as span:

            span.resource = "query %s %s" % (cmd.coll, normalize_filter(operation.spec))
            span.set_tag(mongox.DB, operation.db)
            span.set_tag(mongox.COLLECTION, cmd.coll)
            span.set_tags(cmd.tags)

            result = self.__wrapped__.send_message_with_response(operation, *args, **kwargs)
            if result and result.address:
                span.set_tag(netx.TARGET_HOST, result.address[0])
                span.set_tag(netx.TARGET_PORT, result.address[1])
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

    #  def __getitem__(self, name):
    #      db = self.__wrapped__[name]
    #      return TracedMongoDatabase(self._tracer, self._srv, db)


class TracedMongoCollection(ObjectProxy):

    _tracer = None
    _srv = None
    _collection_name = None

    def __init__(self, tracer, service, database_name, collection):
        super(TracedMongoCollection, self).__init__(collection)
        self._tracer = tracer
        self._srv = service
        self._tags = {
            mongox.COLLECTION: collection.name,
            mongox.DB: database_name,
        }
        self._collection_name = collection.name

    def find(self, filter=None, *args, **kwargs):
        with self.__trace() as span:
            span.set_tags(self._tags)
            nf = '{}'
            if filter:
                nf = normalize_filter(filter)
            span.set_tag(mongox.QUERY, nf)
            span.resource = _create_resource("query", self._collection_name, nf)
            cursor = self.__wrapped__.find(filter=filter, *args, **kwargs)
            _set_cursor_tags(span, cursor)
            return cursor

    def insert_one(self, *args, **kwargs):
        with self.__trace() as span:
            span.resource = _create_resource("insert_one", self._collection_name)
            span.set_tags(self._tags)
            return self.__wrapped__.insert(*args, **kwargs)

    def insert_many(self, *args, **kwargs):
        with self.__trace() as span:
            span.resource = _create_resource("insert_many", self._collection_name)
            span.set_tags(self._tags)
            span.set_tag(mongox.ROWS, len(args[0]))
            return self.__wrapped__.insert_many(*args, **kwargs)

    def delete_one(self, filter):
        with self.__trace() as span:
            nf = '{}'
            if filter:
                nf = normalize_filter(filter)
            span.resource = _create_resource("delete_one", self._collection_name, nf)
            span.set_tags(self._tags)
            return self.__wrapped__.delete_one(filter)

    def delete_many(self, filter):
        with self.__trace() as span:
            nf = '{}'
            if filter:
                nf = normalize_filter(filter)
            span.resource = _create_resource("delete_many", self._collection_name, nf)
            span.set_tags(self._tags)
            return self.__wrapped__.delete_many(filter)

    def __trace(self):
        return self._tracer.trace("pymongo.cmd", span_type=mongox.TYPE, service=self._srv)



class TracedMongoDatabase(ObjectProxy):

    _tracer = None
    _srv = None
    _name = None

    def __init__(self, tracer, service, db):
        super(TracedMongoDatabase, self).__init__(db)
        self._tracer = tracer
        self._srv = service
        self._name = db.name

    def __getattr__(self, name):
        c = getattr(self.__wrapped__, name)
        if isinstance(c, Collection) and not isinstance(c, TracedMongoCollection):
            return TracedMongoCollection(self._tracer, self._srv, self._name, c)
        else:
            return c

    def __getitem__(self, name):
        c = self.__wrapped__[name]
        return TracedMongoCollection(self._tracer, self._srv, self._name, c)

# class TracedMongoClient(ObjectProxy):
#
#     _tracer = None
#     _srv = None
#
#     def __init__(self, tracer, service, client):
#         super(TracedMongoClient, self).__init__(client)
#         self._tracer = tracer
#         self._srv = service
#
#     def __getitem__(self, name):
#         db = self.__wrapped__[name]
#         return TracedMongoDatabase(self._tracer, self._srv, db)

def normalize_filter(f=None):
    if f is None:
        return {}
    elif isinstance(f, list):
        # normalize lists of filters (e.g. {$or: [ { age: { $lt: 30 } }, { type: 1 } ]})
        return [normalize_filter(s) for s in f]
    else:
        # normalize dicts of filters (e.g. {$or: [ { age: { $lt: 30 } }, { type: 1 } ]})
        out = {}
        for k, v in f.iteritems():
            if isinstance(v, list) or isinstance(v, dict):
                # RECURSION ALERT: needs to move to the agent
                out[k] = normalize_filter(v)
            else:
                out[k] = '?'
        return out

def _set_cursor_tags(span, cursor):
    # the address is only set after the cursor is done.
    if cursor and cursor.address:
        span.set_tag(netx.TARGET_HOST, cursor.address[0])
        span.set_tag(netx.TARGET_PORT, cursor.address[1])

def _create_resource(op, collection=None, filter=None):
    if op and collection and filter:
        return "%s %s %s" % (op, collection, filter)
    elif op and collection:
        return "%s %s" % (op, collection)
    else:
        return op

def _resource_from_cmd(cmd):
    if cmd.query is not None:
        nq = normalize_filter(cmd.query)
        return "%s %s %s" % (cmd.name, cmd.coll, nq)
    else:
        return "%s %s" % (cmd.name, cmd.coll)
