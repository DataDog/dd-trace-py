from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.exc import InternalError
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import ProgrammingError

from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.integrations.packages_tests.db_utils import POSTGRES_HOST


def sqli_simple(table):
    engine = create_engine(f"postgresql://postgres:postgres@{POSTGRES_HOST}/postgres")
    with engine.connect() as connection:
        connection.execute(text("SET statement_timeout = 1000"))
        try:
            connection.execute(text("CREATE TABLE students (name TEXT, addr TEXT, city TEXT, pin TEXT)"))
        except (ProgrammingError, OperationalError):
            pass
        rows = []
        try:
            query = text(f"SELECT 1 FROM {table}")
            # label test_sql_injection
            rows = connection.execute(query)
        except InternalError:
            pass
    return {"result": rows, "tainted": is_pyobject_tainted(table), "ranges": str(get_tainted_ranges(table))}
