import json
import logging
import os
import sys
import time
import typing as t
import urllib.error
import urllib.request

from contrib.config import AZURE_COSMOS_EMULATOR_CONFIG
from contrib.config import AZURE_EVENT_HUBS_EMULATOR_CONFIG
from contrib.config import AZURE_SERVICE_BUS_EMULATOR_CONFIG
from contrib.config import AZURE_SQL_EDGE_CONFIG
from contrib.config import AZURITE_CONFIG
from contrib.config import CASSANDRA_CONFIG
from contrib.config import ELASTICSEARCH_CONFIG
from contrib.config import HTTPBIN_CONFIG
from contrib.config import MOTO_CONFIG
from contrib.config import MYSQL_CONFIG
from contrib.config import OPENSEARCH_CONFIG
from contrib.config import POSTGRES_CONFIG
from contrib.config import PUBSUB_CONFIG
from contrib.config import RABBITMQ_CONFIG
from contrib.config import REDIS_CONFIG
from contrib.config import SELENIUM_CONFIG
from contrib.config import VERTICA_CONFIG


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# Golden ratio: consecutive Fibonacci numbers converge to this, so
# `initial_wait * PHI**i` is the closed-form equivalent of scaling by the
# i-th Fibonacci number. Mirrors ddtrace.internal.utils.retry.fibonacci_backoff_with_jitter
# (not importable here: this venv skips the ddtrace dev install), minus the jitter,
# which isn't needed for a local test-service health check.
PHI = 1.618


def try_until_timeout(
    exception,
    tries: int = 100,
    timeout: float = 0.2,
    max_timeout: t.Optional[float] = None,
    args: t.Optional[dict[str, t.Any]] = None,
):
    """Utility decorator that tries to call a check until there is a
    timeout.  The wait between attempts grows following a Fibonacci
    backoff (capped at ``max_timeout``, defaults to ``timeout * 3`` so
    that callers relying on the old fixed-interval budget don't see it
    balloon by default), so that repeated failures back off instead of
    hammering the service at a fixed rate.

    """
    if not args:
        args = {}
    if max_timeout is None:
        max_timeout = timeout * 3

    def wrap(fn):
        def wrapper(**kwargs):
            err = None

            _kwargs = args.copy()
            _kwargs.update(kwargs)

            for i in range(tries):
                try:
                    log.info("Attempt %d: %s(%r)", i, fn.__name__, _kwargs)
                    fn(**_kwargs)
                except ImportError:
                    # A missing client library does not go away with time. Fail now instead of after every retry.
                    raise
                except exception as e:
                    err = e
                    if i < tries - 1:
                        sleep_for = min(timeout * (PHI**i), max_timeout)
                        log.info("Failed: %s. Retrying in %.1fs", e, sleep_for)
                        time.sleep(sleep_for)
                else:
                    break
            else:
                if err:
                    raise err
            log.info("Succeeded: %s", fn.__name__)

        return wrapper

    return wrap


def _http_get(url: str) -> tuple[int, bytes]:
    """Return the status and body of a GET request, like requests.get, which does not raise on HTTP errors."""
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _http_get_ok(url: str) -> bytes:
    """Return the body of a GET request, or raise when the status is an HTTP error, like raise_for_status."""
    status, body = _http_get(url)
    if status >= 400:
        raise Exception(f"GET {url} returned HTTP {status}")
    return body


# The checks that use a client library import the library in the check. The CI job runs the other checks
# with a plain interpreter, without the environment from the wait lockfile.
@try_until_timeout(Exception, args={"pg_config": POSTGRES_CONFIG})
def check_postgres(pg_config):
    from psycopg2 import connect

    conn = connect(**pg_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(Exception, args={"cassandra_config": CASSANDRA_CONFIG})
def check_cassandra(cassandra_config):
    from cassandra.cluster import Cluster

    with Cluster(**cassandra_config).connect() as conn:
        conn.execute("SELECT now() FROM system.local")


@try_until_timeout(Exception, args={"mysql_config": MYSQL_CONFIG})
def check_mysql(mysql_config):
    import mysql.connector

    conn = mysql.connector.connect(**mysql_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(Exception, args={"vertica_config": VERTICA_CONFIG})
def check_vertica(vertica_config):
    import vertica_python

    conn = vertica_python.connect(**vertica_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(Exception, args={"url": "amqp://{user}:{password}@{host}:{port}//".format(**RABBITMQ_CONFIG)})
def check_rabbitmq(url):
    import kombu

    conn = kombu.Connection(url)
    try:
        conn.connect()
    finally:
        conn.release()


@try_until_timeout(Exception, args={"redis_config": REDIS_CONFIG})
def check_redis(redis_config):
    import redis

    client = redis.Redis(**redis_config)
    try:
        client.ping()
    finally:
        client.close()


@try_until_timeout(Exception, args={"url": os.environ.get("DD_TRACE_AGENT_URL", "http://localhost:8126")})
def check_agent(url):
    if not url.endswith("/"):
        url += "/"

    status, _ = _http_get(url)
    if status not in (404, 200):
        raise Exception("Agent not ready")


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/status".format(**SELENIUM_CONFIG)})
def check_selenium(url):
    if not json.loads(_http_get_ok(url)).get("value", {}).get("ready"):
        raise Exception("Selenium not ready")


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/".format(**ELASTICSEARCH_CONFIG)})
def check_elasticsearch(url):
    _http_get_ok(url)


@try_until_timeout(
    Exception, tries=120, timeout=1, args={"url": "http://{host}:{port}/".format(**OPENSEARCH_CONFIG)}
)  # 2 minutes, OpenSearch is slow to start
def check_opensearch(url):
    _http_get_ok(url)


@try_until_timeout(Exception, args={"url": "http://{host}:{port}/".format(**HTTPBIN_CONFIG)})
def check_httpbin(url):
    _http_get_ok(url)


@try_until_timeout(Exception, tries=120, timeout=1, args={"url": "http://{host}:{port}/".format(**MOTO_CONFIG)})
def check_moto(url):
    _http_get_ok(url)


@try_until_timeout(
    Exception,
    tries=120,
    timeout=1,
    args={"url": "http://{host}:{port}/alive".format(**AZURE_COSMOS_EMULATOR_CONFIG)},
)
def check_azurecosmosemulator(url):
    _http_get_ok(url)


@try_until_timeout(
    Exception,
    tries=16,
    timeout=2,
    max_timeout=20,
    args={"url": "http://{host}:{port}/health".format(**AZURE_EVENT_HUBS_EMULATOR_CONFIG)},
)
def check_azureeventhubsemulator(url):
    _http_get_ok(url)


@try_until_timeout(
    Exception,
    tries=16,
    timeout=2,
    max_timeout=20,
    args={"url": "http://{host}:{port}/health".format(**AZURE_SERVICE_BUS_EMULATOR_CONFIG)},
)
def check_azureservicebusemulator(url):
    _http_get_ok(url)


@try_until_timeout(Exception, args={"azuresqledge_config": AZURE_SQL_EDGE_CONFIG})
def check_azuresqledge(azuresqledge_config):
    import pymssql

    conn = pymssql.connect(**azuresqledge_config)
    try:
        conn.cursor().execute("SELECT 1;")
    finally:
        conn.close()


@try_until_timeout(
    Exception,
    tries=120,
    timeout=1,
    args={"url": "http://{host}:{port}/v1/projects/test-project/topics".format(**PUBSUB_CONFIG)},
)
def check_pubsub(url):
    _http_get_ok(url)


@try_until_timeout(Exception, args={"azurite_config": AZURITE_CONFIG})
def check_azurite(azurite_config):
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueServiceClient

    blob_service_client = BlobServiceClient.from_connection_string(
        conn_str=azurite_config["conn_str"], api_version=azurite_config["api_version_blob"]
    )
    _ = list(blob_service_client.list_containers())

    queue_service_client = QueueServiceClient.from_connection_string(
        conn_str=azurite_config["conn_str"], api_version=azurite_config["api_version_queue"]
    )
    _ = list(queue_service_client.list_queues())

    table_service_client = TableServiceClient.from_connection_string(
        conn_str=azurite_config["conn_str"], api_version=azurite_config["api_version_table"]
    )
    _ = list(table_service_client.list_tables())


if __name__ == "__main__":
    check_functions = {
        "cassandra": check_cassandra,
        "ddagent": check_agent,
        "elasticsearch": check_elasticsearch,
        "httpbin": check_httpbin,
        "moto": check_moto,
        "mysql": check_mysql,
        "opensearch": check_opensearch,
        "postgres": check_postgres,
        "pubsub": check_pubsub,
        "rabbitmq": check_rabbitmq,
        "redis": check_redis,
        "selenium-chrome": check_selenium,
        "testagent": check_agent,
        "vertica": check_vertica,
        "azurecosmosemulator": check_azurecosmosemulator,
        "azureeventhubsemulator": check_azureeventhubsemulator,
        "azureservicebusemulator": check_azureservicebusemulator,
        "azuresqledge": check_azuresqledge,
        "azurite": check_azurite,
    }
    if len(sys.argv) >= 2:
        for service in sys.argv[1:]:
            if service not in check_functions:
                log.warning("Unknown service: %s", service)
            else:
                check_functions[service]()
    else:
        print(f"usage: python {sys.argv[0]} SERVICE_NAME")
        sys.exit(1)
