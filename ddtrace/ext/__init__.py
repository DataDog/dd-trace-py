class SpanTypes:
    CACHE = "cache"
    CASSANDRA = "cassandra"
    COSMOS = "cosmosdb"
    ELASTICSEARCH = "elasticsearch"
    GRPC = "grpc"
    GRAPHQL = "graphql"
    HTTP = "http"
    MONGODB = "mongodb"
    REDIS = "redis"
    SERVERLESS = "serverless"
    SQL = "sql"
    TEMPLATE = "template"
    TEST = "test"
    WEB = "web"
    WORKER = "worker"
    AUTH = "auth"
    SYSTEM = "system"
    LLM = "llm"
    VALKEY = "valkey"
    WEBSOCKET = "websocket"
    RAY = "ray"
    PROXY = "proxy"


class SpanKind:
    CLIENT = "client"
    SERVER = "server"
    PRODUCER = "producer"
    CONSUMER = "consumer"
    INTERNAL = "internal"
    PROXY = "proxy"


class SpanLinkKind:
    EXECUTED = "executed_by"
    RESUMING = "resuming"
