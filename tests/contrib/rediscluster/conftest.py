import pytest
import redis

from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.contrib.redis.utils import wait_for_cluster


@pytest.fixture(scope="session", autouse=True)
def wait_for_redis_cluster():
    host = REDISCLUSTER_CONFIG["host"]
    port = int(REDISCLUSTER_CONFIG["ports"].split(",")[0])
    client = redis.Redis(host=host, port=port, decode_responses=True)
    wait_for_cluster(host, port, client)
