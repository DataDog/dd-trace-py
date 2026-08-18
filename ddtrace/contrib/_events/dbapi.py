from dataclasses import dataclass
from enum import Enum

from ddtrace.internal.core.events import Event


class DbApiSpanNamePrefix(str, Enum):
    DB = "db"
    MARIADB = "mariadb"
    MYSQL = "mysql"
    ORACLE = "oracle"
    POSTGRES = "postgres"
    PYMYSQL = "pymysql"
    PYODBC = "pyodbc"
    SQL = "sql"
    SQLITE = "sqlite"
    VERTICA = "vertica"


@dataclass
class DbApiEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "dbapi.query"

    query: str
    span_name_prefix: DbApiSpanNamePrefix
