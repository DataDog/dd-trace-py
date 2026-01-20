from psycopg.errors import DuplicateTable
from psycopg.errors import InFailedSqlTransaction
from psycopg.errors import QueryCanceled

from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.integrations.packages_tests.db_utils import get_psycopg3_connection


def sqli_simple(table):
    connection = get_psycopg3_connection()
    cur = connection.cursor()
    try:
        cur.execute("CREATE TABLE students (name TEXT, addr TEXT, city TEXT, pin TEXT)")
    except (DuplicateTable, QueryCanceled):
        connection.rollback()

    rows = []
    try:
        # label test_sql_injection
        cur.execute("SELECT 1 FROM " + table)
        rows = cur.fetchone()
    except (QueryCanceled, InFailedSqlTransaction):
        connection.rollback()

    connection.close()
    return {"result": rows, "tainted": is_pyobject_tainted(table), "ranges": str(get_tainted_ranges(table))}
