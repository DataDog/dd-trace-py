import time

import freezegun

from ddtrace import tracer
from ddtrace.internal.utils.time import StopWatch


def test_freezegun_does_not_freeze_tracing():
    with freezegun.freeze_time("2020-01-01"):
        with tracer.trace("freezegun.test") as span:
            time.sleep(1)

    assert span.duration >= 1


def test_freezegun_does_not_free_stopwatch():
    with freezegun.freeze_time("2020-01-01"):
        with StopWatch() as sw:
            time.sleep(1)
        assert sw.elapsed() >= 1
