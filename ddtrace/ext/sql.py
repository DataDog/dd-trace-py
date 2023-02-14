from typing import Dict

from ddtrace.internal.compat import ensure_pep562
from ddtrace.vendor.debtcollector import deprecate


# tags
QUERY = "sql.query"  # the query text
ROWS = "sql.rows"  # number of rows returned by a query
DB = "sql.db"  # the name of the database


def normalize_vendor(vendor):
    # type: (str) -> str
    """Return a canonical name for a type of database."""
    if not vendor:
        return "db"  # should this ever happen?
    elif "sqlite" in vendor:
        return "sqlite"
    elif "postgres" in vendor or vendor == "psycopg2":
        return "postgres"
    else:
        return vendor


def parse_pg_dsn(dsn):
    # type: (str) -> Dict[str, str]
    """
    Return a dictionary of the components of a postgres DSN.
    >>> parse_pg_dsn('user=dog port=1543 dbname=dogdata')
    {'user':'dog', 'port':'1543', 'dbname':'dogdata'}
    """
    return dict(_.split("=", 1) for _ in dsn.split())


def __getattr__(name):
    if name == "ROWS":
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            postfix=". Use ddtrace.ext.db.ROWCOUNT instead.",
            removal_version="2.0.0",
        )
        return "sql.rows"

    if name in globals():
        return globals()[name]

    raise AttributeError("'%s' has no attribute '%s'", __name__, name)


ensure_pep562(__name__)
