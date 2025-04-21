from mysql.connector.conversion import MySQLConverter
from psycopg2.extensions import adapt
from psycopg2.extensions import quote_ident
from pymysql.converters import escape_string

from tests.appsec.iast.db_utils import get_psycopg2_connection
from tests.appsec.iast.db_utils import get_pymysql_connection


def adapt_list(obj_list):
    value = adapt(obj_list)
    return value.getquoted()


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
