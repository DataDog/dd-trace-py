from mysql.connector.conversion import MySQLConverter
from psycopg2.extensions import quote_ident
from pymysql.converters import escape_string
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
    return "a-" + mock_conn.escape_string(tainted_value)


def pymysql_converters_escape_string(tainted_value):
    return "a-" + escape_string(tainted_value)
