import sys
import time

import mysql.connector
from psycopg2 import connect, OperationalError
from cassandra.cluster import Cluster, NoHostAvailable

from contrib.config import POSTGRES_CONFIG, CASSANDRA_CONFIG, MYSQL_CONFIG


def try_until_timeout(exception):
    """Utility decorator that tries to call a check until there is a
    timeout.  The default timeout is about 20 seconds.

    """
    def wrap(fn):
        err = None
        def wrapper(*args, **kwargs):
            for i in range(100):
                try:
                    fn()
                except exception as e:
                    err = e
                    time.sleep(0.2)
                else:
                    break
            else:
                if err:
                    raise err
        return wrapper
    return wrap


@try_until_timeout(OperationalError)
def check_postgres():
    conn = connect(**POSTGRES_CONFIG)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(NoHostAvailable)
def check_cassandra():
    with Cluster(**CASSANDRA_CONFIG).connect() as conn:
        conn.execute("SELECT now() FROM system.local")


@try_until_timeout(Exception)
def check_mysql():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


if __name__ == '__main__':
    check_functions = {
        'cassandra': check_cassandra,
        'postgres': check_postgres,
        'mysql': check_mysql
    }
    if len(sys.argv) >= 2:
        for service in sys.argv[1:]:
            check_functions[service]()
    else:
        print("usage: python {} SERVICE_NAME".format(sys.argv[0]))
        sys.exit(1)
