from psycopg2.errors import DuplicateTable

from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from tests.appsec.iast.db_utils import get_connection


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
