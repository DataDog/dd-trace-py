import pytest
import valkey

from tests.contrib.config import VALKEY_CLUSTER_CONFIG
from tests.contrib.redis.utils import wait_for_cluster


@pytest.fixture(scope="session", autouse=True)
def wait_for_valkey_cluster():
    host = VALKEY_CLUSTER_CONFIG["host"]
    port = int(VALKEY_CLUSTER_CONFIG["ports"].split(",")[0])
    client = valkey.Valkey(host=host, port=port)
    wait_for_cluster(host, port, client)
