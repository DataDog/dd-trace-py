from enum import Enum

from ..utils import removed_classproperty
from ..vendor.debtcollector import removals


class SpanTypes(Enum):
    CACHE = "cache"
    CASSANDRA = "cassandra"
    ELASTICSEARCH = "elasticsearch"
    GRPC = "grpc"
    HTTP = "http"
    MONGODB = "mongodb"
    REDIS = "redis"
    SQL = "sql"
    TEMPLATE = "template"
    TEST = "test"
    WEB = "web"
    WORKER = "worker"


@removals.removed_class("AppTypes")
class AppTypes(object):
    @removed_classproperty
    def web(cls):
        return SpanTypes.WEB

    @removed_classproperty
    def db(cls):
        return "db"

    @removed_classproperty
    def cache(cls):
        return SpanTypes.CACHE

    @removed_classproperty
    def worker(cls):
        return SpanTypes.WORKER
