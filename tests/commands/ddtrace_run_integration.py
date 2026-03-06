"""
An integration test that uses a real Redis client
that we expect to be implicitly traced via `ddtrace-run`
"""

import redis

from tests.contrib.config import REDIS_CONFIG
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


if __name__ == "__main__":
    r = redis.Redis(host=REDIS_CONFIG["host"], port=REDIS_CONFIG["port"])
    tracer_scope = scoped_tracer()
    tracer = tracer_scope.__enter__()
    r.flushall()
    spans = TracerSpanContainer(tracer).pop()

    assert len(spans) == 1
    assert spans[0].service == "redis"
    assert spans[0].resource == "FLUSHALL"

    long_cmd = "mget %s" % " ".join(map(str, range(1000)))
    us = r.execute_command(long_cmd)

    spans = TracerSpanContainer(tracer).pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "redis"
    assert span.name == "redis.command"
    assert span.span_type == "redis"
    assert span.error == 0
    assert span._get_numeric_attribute("network.destination.port") == REDIS_CONFIG["port"]
    assert span._get_numeric_attribute("out.redis_db") == 0
    assert span._get_str_attribute("out.host") == REDIS_CONFIG["host"]
    assert span._get_str_attribute("redis.raw_command").startswith("mget 0 1 2 3")
    assert span._get_str_attribute("redis.raw_command").endswith("...")

    tracer_scope.__exit__(None, None, None)
    print("Test success")
