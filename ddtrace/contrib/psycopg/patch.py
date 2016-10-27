
# stdlib
import logging

# 3p
import psycopg2
import wrapt

# project
from ddtrace.contrib import dbapi
from ddtrace.ext import sql, net, db


log = logging.getLogger(__name__)


def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    setattr(_connect, 'datadog_patched_func', psycopg2.connect)
    wrapt.wrap_function_wrapper('psycopg2', 'connect', _connect)

def unpatch():
    """ Unpatch will undo any monkeypatching. """
    connect = getattr(_connect, 'datadog_patched_func', None)
    if connect is not None:
        psycopg2.connect = connect

def wrap(conn, service="postgres", tracer=None):
    """ Wrap will add tracing to the given connection.
        It is only necessary if you aren't monkeypatching
        the library.
    """
    wrapped_conn = dbapi.TracedConnection(conn)

    # fetch tags from the dsn
    dsn = sql.parse_pg_dsn(conn.dsn)
    tags = {
        net.TARGET_HOST: dsn.get("host"),
        net.TARGET_PORT: dsn.get("port"),
        db.NAME: dsn.get("dbname"),
        db.USER: dsn.get("user"),
        "db.application" : dsn.get("application_name"),
    }

    dbapi.configure(
        conn=wrapped_conn,
        service=service,
        name="postgres.query",
        tracer=tracer,
        tags=tags,
    )

    return wrapped_conn


def _connect(connect_func, _, args, kwargs):
    db = connect_func(*args, **kwargs)
    return dbapi.TracedConnection(db)


if __name__ == '__main__':
    import sys
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    print 'PATCHED'
    patch()
    conn = psycopg2.connect(host='localhost', dbname='dogdata', user='dog')
    setattr(conn, "datadog_service", "foo")

    cur = conn.cursor()
    cur.execute("select 'foobar'")
    print cur.fetchall()

    print 'UNPATCHED'
    unpatch()
    conn = psycopg2.connect(host='localhost', dbname='dogdata', user='dog')
    cur = conn.cursor()
    cur.execute("select 'foobar'")
    print cur.fetchall()


