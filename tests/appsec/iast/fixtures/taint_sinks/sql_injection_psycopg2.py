import os

import psycopg2
from psycopg2.errors import DuplicateTable

from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted


POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "127.0.0.1")


def get_connection():
    connection = psycopg2.connect(
        user="postgres", password="postgres", host=POSTGRES_HOST, port="5432", database="postgres"
    )
    return connection


def close_connection(connection):
    if connection:
        connection.close()


def sqli_simple(table):
    connection = get_connection()
    cur = connection.cursor()
    try:
        cur.execute("CREATE TABLE students (name TEXT, addr TEXT, city TEXT, pin TEXT)")
    except DuplicateTable:
        connection.rollback()
    # label test_sql_injection
    cur.execute("SELECT 1 FROM " + table)
    rows = cur.fetchone()
    return {"result": rows, "tainted": is_pyobject_tainted(table), "ranges": str(get_tainted_ranges(table))}
