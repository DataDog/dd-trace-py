from enum import Enum


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
