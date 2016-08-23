"""
The MySQL mysql.connector integration works by creating patched
MySQL connection classes which will trace API calls. For basic usage::

    from ddtrace import tracer
    from ddtrace.contrib.mysql import get_traced_mysql

    # Trace the redis.StrictRedis class ...
    MySQL = get_traced_redis(tracer, service="my-redis-cache")
    conn = MySQL.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")
    conn.set("key", "value")

To use a specific connector, e.g. the C extension module
_mysql_connector::

    import _mysql_connector
    from ddtrace import tracer
    from ddtrace.contrib.mysql import get_traced_mysql_from

    # Trace the _mysql_connector.MySQL class
    MySQL = get_traced_redis_from(tracer, _mysql_connector.MySQL, service="my-mysql-server")
    conn = MySQL.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")
    conn.query("SELECT foo FROM bar;")

Help on mysql.connector can be found on:
https://dev.mysql.com/doc/connector-python/en/
"""


from ..util import require_modules

required_modules = ['mysql.connector']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracers import get_traced_mysql
        from .tracers import get_traced_mysql_from

        __all__ = ['connection_factory']
