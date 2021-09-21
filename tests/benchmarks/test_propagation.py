import pytest

from ddtrace.propagation import http


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_wsgi_large(benchmark):
    benchmark(http.HTTPPropagator.extract, {"HTTP_TEST_HEADER" + str(i): i for i in range(1000)})


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_camel_dash_cased_large(benchmark):
    benchmark(http.HTTPPropagator.extract, {"Test-Header" + str(i): i for i in range(1000)})


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_no_headers(benchmark):
    benchmark(http.HTTPPropagator.extract, {})


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_ideal_scenario(benchmark):
    benchmark(
        http.HTTPPropagator.extract,
        {
            http.HTTP_HEADER_TRACE_ID: 1,
            http.HTTP_HEADER_PARENT_ID: 2,
            http.HTTP_HEADER_SAMPLING_PRIORITY: 1,
            http.HTTP_HEADER_ORIGIN: "benchmarks",
        },
    )


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_invalid_values(benchmark):
    benchmark(
        http.HTTPPropagator.extract,
        {
            http.HTTP_HEADER_TRACE_ID: "one",
            http.HTTP_HEADER_PARENT_ID: "two",
            http.HTTP_HEADER_SAMPLING_PRIORITY: "one",
        },
    )
