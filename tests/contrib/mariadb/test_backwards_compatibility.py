##For Maria DB I don't think this is necesarry, unless Mariadb has an older version that's different. I tested on Mariadb version
## 1.0.6. It looks like https://github.com/mariadb-corporation/mariadb-connector-python/releases?after=v0.9.57 was the first beta release and it doesn't seem to be out of beta yet.
# from ddtrace.contrib.mysql import get_traced_mysql_connection
# from tests.contrib import config
# from tests.utils import DummyTracer


# def test_pre_v4():
#     tracer = DummyTracer()
#     MySQL = get_traced_mysql_connection(tracer, service="my-mysql-server")
#     conn = MySQL(**config.MYSQL_CONFIG)
#     cursor = conn.cursor()
#     cursor.execute("SELECT 1")
#     assert cursor.fetchone()[0] == 1
