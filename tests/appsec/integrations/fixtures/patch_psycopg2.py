from psycopg2.extensions import adapt
from psycopg2.extensions import quote_ident

from tests.appsec.iast.db_utils import get_connection


def adapt_list(obj_list):
    value = adapt(obj_list)
    return value.getquoted()


def sanitize_quote_ident(tainted_value):
    connection = get_connection()
    cur = connection.cursor()
    return "a-" + quote_ident(tainted_value, cur)
