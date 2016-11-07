"""
To trace Postgres calls with the psycopg library::


    from ddtrace import tracer
    from ddtrace.contrib.psycopg import connection_factory


    factory = connection_factory(tracer, service="my-postgres-db")
    db = psycopg2.connect(connection_factory=factory)
    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")
"""


from ..util import require_modules

required_modules = ['psycopg2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .connection import connection_factory
        from .patch import patch, patch_conn

        __all__ = ['connection_factory', 'patch', 'patch_conn']
