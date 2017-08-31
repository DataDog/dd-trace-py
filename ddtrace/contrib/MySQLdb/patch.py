# 3p
import wrapt
import MySQLdb

# project
from ddtrace import Pin
from ddtrace.contrib.dbapi import TracedConnection
from ...ext import net, db


CONN_ATTR_BY_TAG = {
    net.TARGET_HOST : 'host',
    net.TARGET_PORT : 'port',
    db.USER: 'user',
    db.NAME: 'database',
}

def patch():
    wrapt.wrap_function_wrapper('MySQLdb', 'connect', _connect)

def unpatch():
    if isinstance(MySQLdb.connect, wrapt.ObjectProxy):
        MySQLdb.connect = MySQLdb.connect.__wrapped__

def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)

def patch_conn(conn):
    tags = {t: getattr(conn, a) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, '') != ''}
    pin = Pin(service="mysqldb", app="mysqldb", app_type="db", tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn)
    pin.onto(wrapped)
    return wrapped
