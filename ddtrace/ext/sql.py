from typing import Dict


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
