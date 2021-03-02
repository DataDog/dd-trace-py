import pytest

from ddtrace.propagation.utils import from_wsgi_header


@pytest.mark.benchmark(group="propagation-utils", min_time=0.005)
def test_from_wsgi_header(benchmark):
    benchmark(from_wsgi_header, "HTTP_TEST_HEADER")
