import sys
import time

from psycopg2 import connect, OperationalError
from cassandra.cluster import Cluster, NoHostAvailable

from tests.contrib.config import POSTGRES_CONFIG, CASSANDRA_CONFIG


def try_until_timeout(exception):
    """
    Utility decorator that tries to call a check until there is a timeout.
    The default timeout is about 20 seconds.
    """
    def wrap(fn):
        def wrapper(*args, **kwargs):
            for attempt in range(100):
                try:
                    fn()
                except exception:
                    time.sleep(0.2)
                else:
                    break;
            else:
                sys.exit(1)
        return wrapper
    return wrap


# wait for a psycopg2 connection
@try_until_timeout(OperationalError)
def postgresql_check():
    with connect(**POSTGRES_CONFIG) as conn:
        conn.cursor().execute("SELECT 1;")


# wait for cassandra connection
@try_until_timeout(NoHostAvailable)
def cassandra_check():
    with Cluster(**CASSANDRA_CONFIG).connect() as conn:
        conn.execute("SELECT now() FROM system.local")


# checks list
print("Waiting for backing services...")
postgresql_check()
cassandra_check()
print("All backing services are up and running!")
