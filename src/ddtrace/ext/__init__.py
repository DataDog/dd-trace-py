class SpanTypes(object):
    CACHE = "cache"
    CASSANDRA = "cassandra"
    ELASTICSEARCH = "elasticsearch"
    GRPC = "grpc"
    GRAPHQL = "graphql"
    HTTP = "http"
    MONGODB = "mongodb"
    REDIS = "redis"
    SQL = "sql"
    TEMPLATE = "template"
    TEST = "test"
    WEB = "web"
    WORKER = "worker"
    AUTH = "auth"
    SYSTEM = "system"


class SpanKind(object):
    CLIENT = "client"
    SERVER = "server"
    PRODUCER = "producer"
    CONSUMER = "consumer"
