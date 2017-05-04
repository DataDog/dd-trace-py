import sys
import time
import traceback

from psycopg2 import connect, OperationalError
from cassandra.cluster import Cluster, NoHostAvailable

from contrib.config import POSTGRES_CONFIG, CASSANDRA_CONFIG


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


def check():
    print("checking services")
    check_postgres()
    check_cassandra()
    print("services checked")


if __name__ == '__main__':
    check()
