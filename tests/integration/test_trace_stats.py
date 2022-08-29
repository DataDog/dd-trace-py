import os
from typing import Generator

import pytest

from ddtrace import Tracer
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.ext import http
from ddtrace.internal.processor.stats import SpanStatsProcessorV06
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import SamplingRule
from tests.utils import override_global_config

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@pytest.fixture
def sample_rate():
    # type: () -> Generator[float, None, None]
    # Default the sample rate to 0 so no traces are sent for requests.
    yield 0.0


@pytest.fixture
def stats_tracer(sample_rate):
    # type: (float) -> Generator[Tracer, None, None]
    with override_global_config(dict(_trace_compute_stats=True)):
        tracer = Tracer()
        tracer.configure(
            sampler=DatadogSampler(
                default_sample_rate=sample_rate,
            )
        )
        yield tracer
        tracer.shutdown()


@pytest.mark.parametrize("envvar", ["DD_TRACE_STATS_COMPUTATION_ENABLED", "DD_TRACE_COMPUTE_STATS"])
def test_compute_stats_default_and_configure(run_python_code_in_subprocess, envvar):
    """Ensure stats computation can be enabled."""

    # Test enabling via `configure`
    t = Tracer()
    assert not t._compute_stats
    assert not any(isinstance(p, SpanStatsProcessorV06) for p in t._span_processors)
    t.configure(compute_stats_enabled=True)
    assert any(isinstance(p, SpanStatsProcessorV06) for p in t._span_processors)
    assert t._compute_stats

    # Test enabling via environment variable
    env = os.environ.copy()
    env.update({envvar: "true"})
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import tracer
from ddtrace import config
from ddtrace.internal.processor.stats import SpanStatsProcessorV06
assert config._trace_compute_stats is True
assert any(isinstance(p, SpanStatsProcessorV06) for p in tracer._span_processors)
""",
        env=env,
    )
    assert status == 0, out + err


# Can't use a value between 0 and 1 since sampling is not deterministic.
@pytest.mark.parametrize("sample_rate", [1.0, 0.0])
@pytest.mark.snapshot()
def test_sampling_rate(stats_tracer, sample_rate):
    """Ensure traces are sent according to the sampling rate."""
    for _ in range(10):
        with stats_tracer.trace("operation"):
            pass


@pytest.mark.snapshot()
def test_stats_1000(stats_tracer, sample_rate):
    for i in range(1000):
        with stats_tracer.trace("name", service="abc", resource="/users/list"):
            pass


@pytest.mark.snapshot()
def test_stats_errors(stats_tracer, sample_rate):
    for i in range(1000):
        with stats_tracer.trace("name", service="abc", resource="/users/list") as span:
            if i % 2 == 0:
                span.error = 1


@pytest.mark.snapshot()
def test_stats_aggrs(stats_tracer, sample_rate):
    """
    When different span properties are set
        The stats are put into different aggregations
    """
    with stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list"):
        pass

    # Synthetics
    with stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list") as span:
        span.context.dd_origin = "synthetics"

    # HTTP status code
    with stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/list") as span:
        span.set_tag(http.STATUS_CODE, 200)

    # Resource
    with stats_tracer.trace(name="op", service="my-svc", span_type="web", resource="/users/view"):
        pass

    # Service
    with stats_tracer.trace(name="op", service="diff-svc", span_type="web", resource="/users/list"):
        pass

    # Name
    with stats_tracer.trace(name="diff-op", service="my-svc", span_type="web", resource="/users/list"):
        pass

    # Type
    with stats_tracer.trace(name="diff-op", service="my-svc", span_type="db", resource="/users/list"):
        pass


@pytest.mark.snapshot()
def test_measured_span(stats_tracer):
    for _ in range(10):
        with stats_tracer.trace("parent"):  # Should have stats
            with stats_tracer.trace("child"):  # Shouldn't have stats
                pass
    for _ in range(10):
        with stats_tracer.trace("parent"):  # Should have stats
            with stats_tracer.trace("child_stats") as span:  # Should have stats
                span.set_tag(SPAN_MEASURED_KEY)


@pytest.mark.snapshot()
def test_top_level(stats_tracer):
    for _ in range(100):
        with stats_tracer.trace("parent", service="svc-one"):  # Should have stats
            with stats_tracer.trace("child", service="svc-two"):  # Should have stats
                pass


@pytest.mark.parametrize(
    "sampling_rule",
    [SamplingRule(sample_rate=0, service="test"), SamplingRule(sample_rate=1, service="test")],
)
@pytest.mark.snapshot()
def test_single_span_sampling(stats_tracer, sampling_rule):
    sampler = DatadogSampler([sampling_rule])
    stats_tracer.configure(sampler=sampler)
    with stats_tracer.trace("parent", service="test"):
        with stats_tracer.trace("child") as child:
            # FIXME: Replace with span sampling rule
            child.set_metric("_dd.span_sampling.mechanism", 8)
