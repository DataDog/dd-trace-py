"""
testing config.
"""

from ddtrace.internal.settings import env


# default config for backing services
# NOTE: defaults may be duplicated in the .env file; update both or
# simply write down a function that parses the .env file

AZURE_COSMOS_EMULATOR_CONFIG = {
    "host": "127.0.0.1",
    "port": 8080,
}

AZURE_EVENT_HUBS_EMULATOR_CONFIG = {
    "host": "127.0.0.1",
    "port": 5300,
}

AZURE_SERVICE_BUS_EMULATOR_CONFIG = {
    "host": "127.0.0.1",
    "port": 5300,
}

AZURE_SQL_EDGE_CONFIG = {
    "server": "127.0.0.1",
    "user": "sa",
    "password": "Localtestpass1!",
    "database": "master",
    "port": 1433,
}

AZURITE_CONFIG = {
    "api_version_blob": "2025-05-05",
    "api_version_queue": "2025-05-05",
    "api_version_table": "2020-12-06",
    "conn_str": (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
        "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
    ),
}

ELASTICSEARCH_CONFIG = {
    "host": env.get("TEST_ELASTICSEARCH_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_ELASTICSEARCH_PORT", 9200)),
}

OPENSEARCH_CONFIG = {
    "host": env.get("TEST_OPENSEARCH_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_OPENSEARCH_PORT", 9201)),
}

CASSANDRA_CONFIG = {
    "port": int(env.get("TEST_CASSANDRA_PORT", 9042)),
}

CONSUL_CONFIG = {
    "host": "127.0.0.1",
    "port": int(env.get("TEST_CONSUL_PORT", 8500)),
}

# Use host=127.0.0.1 since local docker testing breaks with localhost

POSTGRES_CONFIG = {
    "host": "127.0.0.1",
    "port": int(env.get("TEST_POSTGRES_PORT", 5432)),
    "user": env.get("TEST_POSTGRES_USER", "postgres"),
    "password": env.get("TEST_POSTGRES_PASSWORD", "postgres"),
    "dbname": env.get("TEST_POSTGRES_DB", "postgres"),
}

MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": int(env.get("TEST_MYSQL_PORT", 3306)),
    "user": env.get("TEST_MYSQL_USER", "test"),
    "password": env.get("TEST_MYSQL_PASSWORD", "test"),
    "database": env.get("TEST_MYSQL_DATABASE", "test"),
}

MARIADB_CONFIG = {
    "host": "127.0.0.1",
    "port": int(env.get("TEST_MARIADB_PORT", 3306)),
    "user": env.get("TEST_MARIADB_USER", "test"),
    "password": env.get("TEST_MARIADB_PASSWORD", "test"),
    "database": env.get("TEST_MARIADB_DATABASE", "test"),
}

REDIS_CONFIG = {
    "host": env.get("TEST_REDIS_HOST", "localhost"),
    "port": int(env.get("TEST_REDIS_PORT", 6379)),
}

REDISCLUSTER_CONFIG = {
    "host": "127.0.0.1",
    "ports": env.get("TEST_REDISCLUSTER_PORTS", "7000,7001,7002,7003,7004,7005"),
}

MONGO_CONFIG = {
    "port": int(env.get("TEST_MONGO_PORT", 27017)),
}

MEMCACHED_CONFIG = {
    "host": env.get("TEST_MEMCACHED_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_MEMCACHED_PORT", 11211)),
}

VERTICA_CONFIG = {
    "host": env.get("TEST_VERTICA_HOST", "127.0.0.1"),
    "port": env.get("TEST_VERTICA_PORT", 5433),
    "user": env.get("TEST_VERTICA_USER", "dbadmin"),
    "password": env.get("TEST_VERTICA_PASSWORD", "abc123"),
    "database": env.get("TEST_VERTICA_DATABASE", "docker"),
}

RABBITMQ_CONFIG = {
    "host": env.get("TEST_RABBITMQ_HOST", "127.0.0.1"),
    "user": env.get("TEST_RABBITMQ_USER", "guest"),
    "password": env.get("TEST_RABBITMQ_PASSWORD", "guest"),
    "port": int(env.get("TEST_RABBITMQ_PORT", 5672)),
}

HTTPBIN_CONFIG = {
    "host": env.get("TEST_HTTPBIN_HOST", "localhost"),
    "port": int(env.get("TEST_HTTPBIN_PORT", "8001")),
}

MOTO_CONFIG = {
    "host": env.get("TEST_MOTO_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_MOTO_PORT", "3000")),
}

KAFKA_CONFIG = {
    "host": env.get("TEST_KAFKA_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_KAFKA_PORT", 29092)),
}

PUBSUB_CONFIG = {
    "host": env.get("TEST_PUBSUB_HOST", "127.0.0.1"),
    "port": int(env.get("TEST_PUBSUB_PORT", 8085)),
}

VALKEY_CONFIG = {
    "host": env.get("TEST_VALKEY_HOST", "localhost"),
    "port": int(env.get("TEST_VALKEY_PORT", 6379)),
}

VALKEY_CLUSTER_CONFIG = {
    "host": "127.0.0.1",
    "ports": env.get("TEST_VALKEYCLUSTER_PORTS", "7000,7001,7002,7003,7004,7005"),
}
