import pytest

from ddtrace.propagation import b3
from ddtrace.propagation import datadog
from ddtrace.propagation import http

from ..utils import override_env


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
def test_http_propagator_ideal_scenario_all_propagators(benchmark):
    with override_env(dict(DD_PROPAGATION_STYLE_EXTRACT="B3,Datadog")):
        benchmark(
            http.HTTPPropagator.extract,
            {
                datadog.HTTP_HEADER_TRACE_ID: 1,
                datadog.HTTP_HEADER_PARENT_ID: 2,
                datadog.HTTP_HEADER_SAMPLING_PRIORITY: 1,
                datadog.HTTP_HEADER_ORIGIN: "benchmarks",
                b3.HTTP_HEADER_TRACE_ID: "00000000000000000000000000000001",
                b3.HTTP_HEADER_SPAN_ID: "0000000000000002",
                b3.HTTP_HEADER_SAMPLED: 1,
            },
        )


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_ideal_scenario_datadog(benchmark):
    benchmark(
        datadog.DatadogHTTPPropagator.extract,
        {
            datadog.HTTP_HEADER_TRACE_ID: 1,
            datadog.HTTP_HEADER_PARENT_ID: 2,
            datadog.HTTP_HEADER_SAMPLING_PRIORITY: 1,
            datadog.HTTP_HEADER_ORIGIN: "benchmarks",
        },
    )


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_ideal_scenario_b3(benchmark):
    benchmark(
        b3.B3HTTPPropagator.extract,
        {
            b3.HTTP_HEADER_TRACE_ID: "00000000000000000000000000000001",
            b3.HTTP_HEADER_SPAN_ID: "0000000000000002",
            b3.HTTP_HEADER_SAMPLED: 1,
        },
    )


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_invalid_values_datadog(benchmark):
    benchmark(
        datadog.DatadogHTTPPropagator.extract,
        {
            datadog.HTTP_HEADER_TRACE_ID: "one",
            datadog.HTTP_HEADER_PARENT_ID: "two",
            datadog.HTTP_HEADER_SAMPLING_PRIORITY: "one",
        },
    )


@pytest.mark.benchmark(group="HTTPPropagator.extract")
def test_extract_invalid_values_b3(benchmark):
    benchmark(
        b3.B3HTTPPropagator.extract,
        {
            b3.HTTP_HEADER_TRACE_ID: "one",
            b3.HTTP_HEADER_SPAN_ID: "two",
            b3.HTTP_HEADER_SAMPLED: "one",
        },
    )
