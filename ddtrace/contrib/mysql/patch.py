# 3p
import wrapt
import mysql.connector

# project
from ddtrace import Pin
from ddtrace.contrib.dbapi import TracedConnection
from ...ext import net, db


CONN_ATTR_BY_TAG = {
    net.TARGET_HOST : 'server_host',
    net.TARGET_PORT : 'server_port',
    db.USER: 'user',
    db.NAME: 'database',
}

def patch():
    wrapt.wrap_function_wrapper('mysql.connector', 'connect', _connect)
    # `Connect` is an alias for `connect`, patch it too
    if hasattr(mysql.connector, 'Connect'):
        mysql.connector.Connect = mysql.connector.connect

def unpatch():
    if isinstance(mysql.connector.connect, wrapt.ObjectProxy):
        mysql.connector.connect = mysql.connector.connect.__wrapped__
        if hasattr(mysql.connector, 'Connect'):
            mysql.connector.Connect = mysql.connector.connect

def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)

def patch_conn(conn):
    # default pin
    pin = Pin(service="mysql", app="mysql")

    # grab the metadata from the conn
    pin.tags = {}
    for tag, attr in CONN_ATTR_BY_TAG.items():
        pin.tags[tag] = getattr(conn, attr, '')

    wrapped = TracedConnection(conn)
    pin.onto(wrapped)
    return wrapped
