
# stdlib
import logging

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


log = logging.getLogger(__name__)


def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    wrapt.wrap_function_wrapper('mysql.connector', 'connect', _connect)
    if hasattr(mysql.connector, 'Connect'):
        mysql.connector.Connect = mysql.connector.connect

def unpatch():
    if isinstance(mysql.connector.connect, wrapt.ObjectProxy):
        mysql.connector.connect = mysql.connector.connect.__wrapped__
        if hasattr(mysql.connector, 'Connect'):
            mysql.connector.Connect = mysql.connector.connect

def patch_conn(conn, pin=None):
    if not pin:
        pin = Pin(service="mysql", app="mysql")

    # grab the metadata from the conn
    pin.tags = pin.tags or {}
    for tag, attr in CONN_ATTR_BY_TAG.items():
        pin.tags[tag] = getattr(conn, attr, '')

    wrapped = TracedConnection(conn)
    pin.onto(wrapped)
    return wrapped


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)

# deprecated
def get_traced_mysql_connection(*args, **kwargs):
    log.warn("get_traced_mysql_connection is deprecated")
    return mysql.connector.MySQLConnection
