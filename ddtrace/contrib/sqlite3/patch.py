# stdlib
import logging

# 3p
import sqlite3
import wrapt

# project
from ddtrace.contrib.dbapi import TracedConnection


log = logging.getLogger(__name__)


def _connect(connect_func, _, args, kwargs):
    db = connect_func(*args, **kwargs)
    return TracedConnection(db)

def unpatch():
    """ unpatch undoes any monkeypatching. """
    connect = getattr(_connect, 'datadog_patched_func', None)
    if connect is not None:
        sqlite3.connect = connect


def patch():
    """
    patch monkey patches psycopg's connection class so all

    new connections will be traced by default.
    """
    setattr(_connect, 'datadog_patched_func', sqlite3.connect)
    wrapt.wrap_function_wrapper('sqlite3', 'connect', _connect)


if __name__ == '__main__':
    import sys
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    print 'PATCHED'
    patch()
    db = sqlite3.connect(":memory:")
    setattr(db, "datadog_service", "foo")

    cur = db.cursor()
    cur.execute("create table foo ( bar text)")
    cur.execute("select * from sqlite_master")
    print cur.fetchall()

    print 'UNPATCHED'
    unpatch()
    db = sqlite3.connect(":memory:")
    cur = db.cursor()
    cur.execute("select 'foobar'")
    print cur.fetchall()


