"""
An integration test that uses a real Redis client
that we expect to be implicitly traced via `ddtrace-run`
"""

import redis

from ddtrace._trace.pin import Pin
from tests.contrib.config import REDIS_CONFIG
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


if __name__ == "__main__":
    r = redis.Redis(host=REDIS_CONFIG["host"], port=REDIS_CONFIG["port"])
    pin = Pin.get_from(r)
    assert pin

    tracer_scope = scoped_tracer()
    pin._tracer = tracer_scope.__enter__()
    with TracerSpanContainer(pin._tracer) as tsc:
        r.flushall()
        spans = tsc.pop_spans()

        assert len(spans) == 1
        assert spans[0].service == "redis"
        assert spans[0].resource == "FLUSHALL"

        long_cmd = "mget %s" % " ".join(map(str, range(1000)))
        us = r.execute_command(long_cmd)

        spans = tsc.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_metric("network.destination.port") == REDIS_CONFIG["port"]
        assert span.get_metric("out.redis_db") == 0
        assert span.get_tag("out.host") == REDIS_CONFIG["host"]
        assert span.get_tag("redis.raw_command").startswith("mget 0 1 2 3")
        assert span.get_tag("redis.raw_command").endswith("...")

    tracer_scope.__exit__(None, None, None)
    print("Test success")
