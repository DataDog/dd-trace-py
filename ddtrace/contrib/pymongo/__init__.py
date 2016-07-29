
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
    _service = None

    def __init__(self, tracer, service, database_name, collection):
        super(TracedMongoCollection, self).__init__(collection)
        self._tracer = tracer
        self._service = service
        self._tags = {
            mongox.COLLECTION: collection.name,
            mongox.DB: database_name,

        }

    def find(self, filter=None, *args, **kwargs):
        with self._tracer.trace("pymongo.find", service=self._service) as span:
            span.set_tags(self._tags)
            span.set_tag(mongox.QUERY, normalize_filter(filter))
            cursor = self.__wrapped__.find(*args, **kwargs)
            try:
                _set_cursor_tags(span, cursor)
            finally:
                return cursor

    def insert_one(self, *args, **kwargs):
        with self._tracer.trace("pymongo.insert_one", service=self._service) as span:
            span.set_tags(self._tags)
            return self.__wrapped__.insert(*args, **kwargs)

    def insert_many(self, *args, **kwargs):
        with self._tracer.trace("pymongo.insert_many", service=self._service) as span:
            span.set_tags(self._tags)
            span.set_tag(mongox.ROWS, len(args[0]))
            return self.__wrapped__.insert_many(*args, **kwargs)


class TracedMongoDatabase(ObjectProxy):

    _tracer = None
    _service = None
    _name = None

    def __init__(self, tracer, service, db):
        super(TracedMongoDatabase, self).__init__(db)
        self._tracer = tracer
        self._service = service
        self._name = db.name

    def __getattr__(self, name):
        c = getattr(self.__wrapped__, name)
        if isinstance(c, Collection) and not isinstance(c, TracedMongoCollection):
            return TracedMongoCollection(self._tracer, self._service, self._name, c)
        else:
            return c

    def __getitem__(self, name):
        c = self.__wrapped__[name]
        return TracedMongoCollection(self._tracer, self._service, self._name, c)

class TracedMongoClient(ObjectProxy):

    _tracer = None
    _service = None

    def __init__(self, tracer, service, client):
        super(TracedMongoClient, self).__init__(client)
        self._tracer = tracer
        self._service = service

    def __getitem__(self, name):
        db = self.__wrapped__[name]
        return TracedMongoDatabase(self._tracer, self._service, db)


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

