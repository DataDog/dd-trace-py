import pytest

import ddtrace
from ddtrace._trace.processor import SpanAggregator
from ddtrace._trace.processor import TraceProcessor
from ddtrace.contrib.internal import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import NativeWriter
from ddtrace.trace import Span
from tests.appsec.utils import asm_context
from tests.utils import DummyTracer
from tests.utils import override_env


class DummyProcessor(TraceProcessor):
    def process_trace(self, trace):
        return trace


@pytest.fixture
def int_config():
    c = Config()
    c.myint = IntegrationConfig(c, "myint")
    return c


@pytest.fixture
def span(tracer):
    with tracer.trace(name="myint") as span:
        yield span


@pytest.mark.parametrize("writer_class", (AgentWriter, NativeWriter))
def test_aggregator_reset_with_args(writer_class):
    """
    Validates that the span aggregator can reset trace buffers, sampling processor,
    user processors/filters and trace api version (when ASM is enabled)
    """

    dd_proc = DummyProcessor()
    user_proc = DummyProcessor()
    aggr = SpanAggregator(
        partial_flush_enabled=False,
        partial_flush_min_spans=1,
        dd_processors=[dd_proc],
        user_processors=[user_proc],
    )

    aggr.writer = writer_class("http://localhost:8126", api_version="v0.5")
    span = Span("span", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(span)

    # Expect SpanAggregator to have the expected processors, api_version and span in _traces
    assert dd_proc in aggr.dd_processors
    assert user_proc in aggr.user_processors
    assert span.trace_id in aggr._traces
    assert len(aggr._span_metrics["spans_created"]) == 1
    assert aggr.writer._api_version == "v0.5"
    # Expect the default value of apm_opt_out and compute_stats to be False
    assert aggr.sampling_processor.apm_opt_out is False
    assert aggr.sampling_processor._compute_stats_enabled is False
    # Reset the aggregator with new args and new user processors and expect the new values to be set
    aggr.reset(user_processors=[], compute_stats=True, apm_opt_out=True, appsec_enabled=True, reset_buffer=False)
    assert aggr.user_processors == []
    assert dd_proc in aggr.dd_processors
    assert aggr.sampling_processor.apm_opt_out is True
    assert aggr.sampling_processor._compute_stats_enabled is True
    assert aggr.writer._api_version == "v0.4"
    assert span.trace_id in aggr._traces
    assert len(aggr._span_metrics["spans_created"]) == 1


@pytest.mark.parametrize("appsec_enabled", [False, True])
@pytest.mark.parametrize("span_type", [SpanTypes.WEB, SpanTypes.HTTP, None])
@pytest.mark.parametrize(
    "method,url,status_code,status_msg,query,request_headers,response_headers,uri,path_params,cookies,target_host",
    [
        ("GET", "http://localhost/", 0, None, None, None, None, None, None, None, "localhost"),
        ("GET", "http://localhost/", 200, "OK", None, None, None, None, None, None, "localhost"),
        (None, None, None, None, None, None, None, None, None, None, None),
        (
            "GET",
            "http://localhost/",
            200,
            "OK",
            None,
            {"my-header": "value1"},
            {"resp-header": "val"},
            "http://localhost/",
            None,
            None,
            "localhost",
        ),
        (
            "GET",
            "http://localhost/",
            200,
            "OK",
            "q=test+query&q2=val",
            {"my-header": "value1"},
            {"resp-header": "val"},
            "http://localhost/search?q=test+query&q2=val",
            {"id": "val", "name": "vlad"},
            None,
            "localhost",
        ),
        ("GET", "http://user:pass@localhost/", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://user@localhost/", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://user:pass@localhost/api?q=test", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://localhost/api@test", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://localhost/?api@test", 0, None, None, None, None, None, None, None, None),
    ],
)
def test_set_http_meta(
    span,
    int_config,
    method,
    url,
    target_host,
    status_code,
    status_msg,
    query,
    request_headers,
    response_headers,
    uri,
    path_params,
    cookies,
    appsec_enabled,
    span_type,
):
    int_config.myint.http.trace_headers(["my-header"])
    int_config.myint.http.trace_query_string = True
    span.span_type = span_type
    with asm_context(config={"_asm_enabled": appsec_enabled}):
        trace_utils.set_http_meta(
            span,
            int_config.myint,
            method=method,
            url=url,
            target_host=target_host,
            status_code=status_code,
            status_msg=status_msg,
            query=query,
            raw_uri=uri,
            request_headers=request_headers,
            response_headers=response_headers,
            request_cookies=cookies,
            request_path_params=path_params,
        )
    if method is not None:
        assert span.get_tag(http.METHOD) == method
    else:
        assert http.METHOD not in span.get_tags()

    if target_host is not None:
        assert span.get_tag(net.TARGET_HOST) == target_host
    else:
        assert net.TARGET_HOST not in span.get_tags()

    if url is not None:
        if url.startswith("http://user"):
            # Remove any userinfo that may be in the original url
            expected_url = url[: url.index(":")] + "://" + url[url.index("@") + 1 :]
        else:
            expected_url = url

        if query and int_config.myint.http.trace_query_string:
            assert span.get_tag(http.URL) == str(expected_url + "?" + query)
        else:
            assert span.get_tag(http.URL) == str(expected_url)
    else:
        assert http.URL not in span.get_tags()

    if status_code is not None:
        assert span.get_tag(http.STATUS_CODE) == str(status_code)
        if 500 <= int(status_code) < 600:
            assert span.error == 1
        else:
            assert span.error == 0
    else:
        assert http.STATUS_CODE not in span.get_tags()

    if status_msg is not None:
        assert span.get_tag(http.STATUS_MSG) == str(status_msg)

    if query is not None and int_config.myint.http.trace_query_string:
        assert span.get_tag(http.QUERY_STRING) == query

    if request_headers is not None:
        for header, value in request_headers.items():
            tag = "http.request.headers." + header
            assert span.get_tag(tag) == value


@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_configuration(sca_enabled, appsec_enabled, iast_enabled):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with override_env({"DD_APPSEC_SCA_ENABLED": sca_enabled}):
        ddtrace.config._reset()
        tracer = DummyTracer()
        tracer.configure(appsec_enabled=appsec_enabled, iast_enabled=iast_enabled, apm_tracing_disabled=True)
        if sca_enabled == "true":
            assert bool(ddtrace.config._sca_enabled) is True
        assert tracer.enabled is False

        assert isinstance(tracer._sampler.limiter, RateLimiter)
        assert tracer._sampler.limiter.rate_limit == 1
        assert tracer._sampler.limiter.time_window == 60e9

        assert tracer._span_aggregator.sampling_processor._compute_stats_enabled is False

    # reset tracer values
    with override_env({"DD_APPSEC_SCA_ENABLED": "false"}):
        ddtrace.config._reset()
        tracer.configure(appsec_enabled=False, iast_enabled=False, apm_tracing_disabled=False)
