from typing import Callable
from typing import Optional

from mysql.connector.conversion import MySQLConverter
from psycopg2.extensions import quote_ident
from pymysql.converters import escape_string  # type: ignore[import-untyped]
from werkzeug.utils import safe_join
from werkzeug.utils import secure_filename

from tests.appsec.integrations.packages_tests.db_utils import get_psycopg2_connection
from tests.appsec.integrations.packages_tests.db_utils import get_pymysql_connection


def werkzeug_secure_filename(tainted_value):
    return "a-" + secure_filename(tainted_value)


def werkzeug_secure_safe_join(tainted_value):
    base_dir = "/var/www/uploads"
    return safe_join(base_dir, tainted_value)


def html_scape(tainted_value):
    from html import escape

    return escape(tainted_value)


def markupsafe_scape(tainted_value):
    from markupsafe import escape

    return str(escape(tainted_value))


def sanitize_quote_ident(tainted_value):
    connection = get_psycopg2_connection()
    cur = connection.cursor()
    return "a-" + quote_ident(tainted_value, cur)


def mysql_connector_scape(tainted_value):
    converter = MySQLConverter()
    return "a-" + converter.escape(tainted_value)


def pymysql_escape_string(tainted_value):
    mock_conn = get_pymysql_connection()
    escape: Callable[..., str] = getattr(mock_conn, "_escape_string", None) or mock_conn.escape_string
    return "a-" + escape(tainted_value)


def pymysql_underscore_escape_string_without_public_alias(tainted_value: str) -> str:
    """Call ``Connection._escape_string`` as PyMySQL 1.2 does (no public ``escape_string``).

    Uses ``NO_BACKSLASH_ESCAPES`` so the implementation does not fall through to
    ``converters.escape_string`` (already wrapped on older IAST). No live MySQL.
    """
    from pymysql.connections import Connection  # type: ignore[import-untyped]

    try:
        from pymysql.constants.SERVER_STATUS import SERVER_STATUS_NO_BACKSLASH_ESCAPES  # type: ignore[import-untyped]
    except ImportError:
        SERVER_STATUS_NO_BACKSLASH_ESCAPES = 512

    no_backslash: int = SERVER_STATUS_NO_BACKSLASH_ESCAPES
    public_escape: Optional[object] = getattr(Connection, "escape_string", None)
    if public_escape is not None:
        delattr(Connection, "escape_string")
    try:
        conn: Connection = object.__new__(Connection)
        conn.server_status = no_backslash
        escaped: str = str(conn._escape_string(tainted_value))
        return "a-" + escaped
    finally:
        if public_escape is not None:
            setattr(Connection, "escape_string", public_escape)


def pymysql_converters_escape_string(tainted_value):
    return "a-" + escape_string(tainted_value)
