
# 3p
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from wrapt import ObjectProxy

# project
from ...ext import AppTypes
from ...ext import mongo as mongox
from ...ext import net as netx


def trace_mongo_client(client, tracer, service="mongodb"):
    tracer.set_service_info(
        service=service,
        app=mongox.TYPE,
        app_type=AppTypes.db,
    )
    return TracedMongoClient(tracer, service, client)


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
        with self._tracer.trace("pymongo.command", span_type=mongox.TYPE, service=self._srv) as span:
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
        with self._tracer.trace("pymongo.command", span_type=mongox.TYPE, service=self._srv) as span:
            span.resource = _create_resource("insert_one", self._collection_name)
            span.set_tags(self._tags)
            return self.__wrapped__.insert(*args, **kwargs)

    def insert_many(self, *args, **kwargs):
        with self._tracer.trace("pymongo.command", span_type=mongox.TYPE, service=self._srv) as span:
            span.resource = _create_resource("insert_many", self._collection_name)
            span.set_tags(self._tags)
            span.set_tag(mongox.ROWS, len(args[0]))
            return self.__wrapped__.insert_many(*args, **kwargs)


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

class TracedMongoClient(ObjectProxy):

    _tracer = None
    _srv = None

    def __init__(self, tracer, service, client):
        super(TracedMongoClient, self).__init__(client)
        self._tracer = tracer
        self._srv = service

    def __getitem__(self, name):
        db = self.__wrapped__[name]
        return TracedMongoDatabase(self._tracer, self._srv, db)

def normalize_filter(f=None):
    if f is None:
        return {}
    if isinstance(f, list):
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

