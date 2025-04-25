import sqlite3

from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted


def sqli_simple(table):
    con = sqlite3.connect(":memory:")
    cur = con.cursor()

    cur.execute("CREATE TABLE students (name TEXT, addr TEXT, city TEXT, pin TEXT)")
    # label test_sql_injection
    cur.execute("SELECT 1 FROM " + table)
    rows = cur.fetchall()
    return {"result": rows, "tainted": is_pyobject_tainted(table), "ranges": str(get_tainted_ranges(table))}
