
from ddtrace.contrib.mysql import get_traced_mysql_connection
from tests.test_tracer import DummyTracer
from tests.contrib import config


def test_pre_v4():
    tracer = DummyTracer()
    MySQL = get_traced_mysql_connection(tracer, service='my-mysql-server')
    conn = MySQL(**config.MYSQL_CONFIG)
    cursor = conn.cursor()
    cursor.execute('SELECT 1')
    assert cursor.fetchone()[0] == 1
