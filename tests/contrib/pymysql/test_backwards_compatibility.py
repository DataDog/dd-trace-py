from ddtrace.contrib.pymysql import get_traced_pymysql_connection
from tests.tracer.test_tracer import get_dummy_tracer
from tests.contrib import config


def test_pre_v4():
    tracer = get_dummy_tracer()
    MySQL = get_traced_pymysql_connection(tracer, service="my-mysql-server")
    conn = MySQL(**config.MYSQL_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
