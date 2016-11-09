import sys
import time
import traceback

from contrib.config import POSTGRES_CONFIG, CASSANDRA_CONFIG

def try_until_timeout(exception):
    """
    Utility decorator that tries to call a check until there is a timeout.
    The default timeout is about 20 seconds.
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

def check_postgres():
    try:
        from psycopg2 import connect, OperationalError
    except ImportError:
        return False

    @try_until_timeout(OperationalError)
    def _ping():
        conn = connect(**POSTGRES_CONFIG)
        try:
            conn.cursor().execute("SELECT 1;")
        finally:
            conn.close()

    _ping()


def check_cassandra():
    try:
        from cassandra.cluster import Cluster, NoHostAvailable
    except ImportError:
        return False

    # wait for cassandra connection
    @try_until_timeout(NoHostAvailable)
    def _ping():
        with Cluster(**CASSANDRA_CONFIG).connect() as conn:
            conn.execute("SELECT now() FROM system.local")

    _ping()


def check():
    print("checking services")
    check_postgres()
    check_cassandra()
    print("services checked")

if __name__ == '__main__':
    check()

