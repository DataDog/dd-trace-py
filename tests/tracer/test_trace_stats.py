import os
from typing import Generator

import pytest

from ddtrace import Tracer
from ddtrace.internal.processor.stats import SpanStatsProcessorV06
from ddtrace.sampler import DatadogSampler
from tests.utils import override_global_config


@pytest.fixture
def sample_rate():
    # type: () -> Generator[float, None, None]
    yield 1.0


@pytest.fixture
def stats_tracer(sample_rate):
    # type: (float) -> Generator[Tracer, None, None]
    with override_global_config(dict(_compute_trace_stats=True)):
        tracer = Tracer()
        tracer.configure(
            sampler=DatadogSampler(
                default_sample_rate=sample_rate,
            )
        )
        yield tracer


def test_compute_stats_default_and_configure(run_python_code_in_subprocess):
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
    env.update({"DD_TRACE_COMPUTE_STATS": "true"})
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


@pytest.mark.parametrize("sample_rate", [1.0, 0.5, 0.0])
@pytest.mark.testagent
def test_stats_sampling_rate(stats_tracer, sample_rate, testagent):
    """Ensure traces are sent according to the sampling rate."""
    for _ in range(1000):
        with stats_tracer.trace("operation"):
            pass
    print(testagent)
