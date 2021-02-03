import pytest


@pytest.mark.benchmark(group="span-id", min_time=0.005)
def test_rand64bits_no_pid(benchmark):
    from ddtrace.internal import _rand

    benchmark(_rand.rand64bits, False)


@pytest.mark.benchmark(group="span-id", min_time=0.005)
def test_rand64bits_pid_check(benchmark):
    from ddtrace.internal import _rand

    benchmark(_rand.rand64bits)


@pytest.mark.benchmark(group="span-id", min_time=0.005)
def test_randbits_stdlib(benchmark):
    from ddtrace.compat import getrandbits

    benchmark(getrandbits, 64)
