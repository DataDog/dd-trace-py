import time

import pytest

from ddtrace import tracer as dd_tracer
from ddtrace.internal.utils.time import StopWatch


@pytest.fixture
def _patch_freezegun():
    from ddtrace.contrib.freezegun import patch
    from ddtrace.contrib.freezegun import unpatch

    patch()
    yield
    unpatch()


def test_freezegun_does_not_freeze_tracing(_patch_freezegun):
    import freezegun

    with freezegun.freeze_time("2020-01-01"):
        with dd_tracer.trace("freezegun.test") as span:
            time.sleep(1)

    assert span.duration >= 1


def test_freezegun_does_not_freeze_stopwatch(_patch_freezegun):
    import freezegun

    with freezegun.freeze_time("2020-01-01"):
        with StopWatch() as sw:
            time.sleep(1)
        assert sw.elapsed() >= 1
