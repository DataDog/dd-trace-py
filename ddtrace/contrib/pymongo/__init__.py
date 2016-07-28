
# 3p
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from wrapt import ObjectProxy

# project
from ...ext import AppTypes


class TracedMongoCollection(ObjectProxy):

    _tracer = None
    _service = None

    def __init__(self, tracer, service, collection):
        super(TracedMongoCollection, self).__init__(collection)
        self._tracer = tracer
        self._service = service

    def find(self, *args, **kwargs):
        with self._tracer.trace("pymongo.find", service=self._service) as span:
            return self.__wrapped__.find(*args, **kwargs)

    def insert_one(self, *args, **kwargs):
        with self._tracer.trace("pymongo.insert_one", service=self._service) as span:
            return self.__wrapped__.insert(*args, **kwargs)

    def insert_many(self, *args, **kwargs):
        with self._tracer.trace("pymongo.insert_many", service=self._service) as span:
            return self.__wrapped__.insert_many(*args, **kwargs)

class TracedMongoDatabase(ObjectProxy):

    _tracer = None
    _service = None

    def __init__(self, tracer, service, db):
        super(TracedMongoDatabase, self).__init__(db)
        self._tracer = tracer
        self._service = service

    def __getattr__(self, name):
        c = getattr(self.__wrapped__, name)
        if isinstance(c, Collection) and not isinstance(c, TracedMongoCollection):
            return TracedMongoCollection(self._tracer, self._service, c)
        else:
            return c

    def __getitem__(self, name):
        c = self.__wrapped__[name]
        return TracedMongoCollection(self._tracer, self._service, c)

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


def trace_mongo_client(client, tracer, service="mongodb"):
    tracer.set_service_info(
        service=service,
        app="mongodb",
        app_type=AppTypes.db,
    )
    return TracedMongoClient(tracer, service, client)
