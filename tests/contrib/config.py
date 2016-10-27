"""
testing config.
"""

import os


# default config for backing services
# NOTE: defaults may be duplicated in the .env file; update both or
# simply write down a function that parses the .env file

ELASTICSEARCH_CONFIG = {
    'port': int(os.getenv("TEST_ELASTICSEARCH_PORT", 59200)),
}

CASSANDRA_CONFIG = {
    'port': int(os.getenv("TEST_CASSANDRA_PORT", 59042)),
}

POSTGRES_CONFIG = {
    'host' : 'localhost',
    'port': int(os.getenv("TEST_POSTGRES_PORT", 5432)),
    'user' : os.getenv("TEST_POSTGRES_USER", "dog"),
    'password' : os.getenv("TEST_POSTGRES_PASSWORD", "dog"),
    'dbname' : os.getenv("TEST_POSTGRES_DB", "dogdata"),
}

MYSQL_CONFIG = {
    'host' : '127.0.0.1',
    'port' : int(os.getenv("TEST_MYSQL_PORT", 53306)),
    'user' : os.getenv("TEST_MYSQL_USER", 'test'),
    'password' : os.getenv("TEST_MYSQL_PASSWORD", 'test'),
    'database' : os.getenv("TEST_MYSQL_DATABASE", 'test'),
}

REDIS_CONFIG = {
    'port': int(os.getenv("TEST_REDIS_PORT", 56379)),
}

MONGO_CONFIG = {
    'port': int(os.getenv("TEST_MONGO_PORT", 57017)),
}

MEMCACHED_CONFIG = {
    'host' : os.getenv('TEST_MEMCACHED_HOST', '127.0.0.1'),
    'port': int(os.getenv("TEST_MEMCACHED_PORT", 51211)),
}
