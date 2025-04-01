import logging
import os
import sys
import time
import typing as t

from cassandra.cluster import Cluster
from cassandra.cluster import NoHostAvailable
from contrib.config import CASSANDRA_CONFIG
from contrib.config import ELASTICSEARCH_CONFIG
from contrib.config import HTTPBIN_CONFIG
from contrib.config import MOTO_CONFIG
from contrib.config import MYSQL_CONFIG
from contrib.config import OPENSEARCH_CONFIG
from contrib.config import POSTGRES_CONFIG
from contrib.config import PYGOAT_CONFIG
from contrib.config import RABBITMQ_CONFIG
from contrib.config import VERTICA_CONFIG
import kombu
import mysql.connector
from psycopg2 import OperationalError
from psycopg2 import connect
import requests
import vertica_python


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def try_until_timeout(exception, tries: int = 100, timeout: float = 0.2, args: t.Optional[t.Dict[str, t.Any]] = None):
    """Utility decorator that tries to call a check until there is a
    timeout.  The default timeout is about 20 seconds.

    """
    if not args:
        args = {}

    def wrap(fn):
        def wrapper(**kwargs):
            err = None

            _kwargs = args.copy()
            _kwargs.update(kwargs)

            for i in range(tries):
                try:
                    log.info("Attempt %d: %s(%r)", i, fn.__name__, _kwargs)
                    fn(**_kwargs)
                except exception as e:
                    err = e
                    time.sleep(timeout)
                else:
                    break
            else:
                if err:
                    raise err
            log.info("Succeeded: %s", fn.__name__)

        return wrapper

    return wrap


@try_until_timeout(OperationalError, args={"pg_config": POSTGRES_CONFIG})
def check_postgres(pg_config):
    conn = connect(**pg_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(NoHostAvailable, args={"cassandra_config": CASSANDRA_CONFIG})
def check_cassandra(cassandra_config):
    with Cluster(**cassandra_config).connect() as conn:
        conn.execute("SELECT now() FROM system.local")


@try_until_timeout(Exception, args={"mysql_config": MYSQL_CONFIG})
def check_mysql(mysql_config):
    conn = mysql.connector.connect(**mysql_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(Exception, args={"vertica_config": VERTICA_CONFIG})
def check_vertica(vertica_config):
    conn = vertica_python.connect(**vertica_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(Exception, args={"url": "amqp://{user}:{password}@{host}:{port}//".format(**RABBITMQ_CONFIG)})
def check_rabbitmq(url):
    conn = kombu.Connection(url)
    try:
        conn.connect()
    finally:
        conn.release()


@try_until_timeout(Exception, args={"url": os.environ.get("DD_TRACE_AGENT_URL", "http://localhost:8126")})
def check_agent(url):
    if not url.endswith("/"):
        url += "/"

    res = requests.get(url)
    if res.status_code not in (404, 200):
        raise Exception("Agent not ready")


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/".format(**ELASTICSEARCH_CONFIG)})
def check_elasticsearch(url):
    requests.get(url).raise_for_status()


@try_until_timeout(
    Exception, tries=120, timeout=1, args={"url": "http://{host}:{port}/".format(**OPENSEARCH_CONFIG)}
)  # 2 minutes, OpenSearch is slow to start
def check_opensearch(url):
    requests.get(url).raise_for_status()


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/".format(**HTTPBIN_CONFIG)})
def check_httpbin(url):
    requests.get(url).raise_for_status()


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/".format(**PYGOAT_CONFIG)})
def check_pygoat(url):
    requests.get(url).raise_for_status()

@try_until_timeout(Exception, tries=120, timeout=1, args={"url": "http://{host}:{port}/".format(**MOTO_CONFIG)})
def check_moto(url):
    requests.get(url).raise_for_status()


if __name__ == "__main__":
    check_functions = {
        "cassandra": check_cassandra,
        "ddagent": check_agent,
        "elasticsearch": check_elasticsearch,
        "httpbin_local": check_httpbin,
        "moto": check_moto,
        "mysql": check_mysql,
        "opensearch": check_opensearch,
        "postgres": check_postgres,
        "rabbitmq": check_rabbitmq,
        "testagent": check_agent,
        "vertica": check_vertica,
    }
    if len(sys.argv) >= 2:
        for service in sys.argv[1:]:
            if service not in check_functions:
                log.warning("Unknown service: %s", service)
            else:
                check_functions[service]()
    else:
        print("usage: python {} SERVICE_NAME".format(sys.argv[0]))
        sys.exit(1)
