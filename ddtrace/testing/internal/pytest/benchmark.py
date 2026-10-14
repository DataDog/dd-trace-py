"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.benchmark`` moved to:

``ddtrace_testing.internal.pytest.benchmark``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.benchmark import BENCHMARK_INFO_TAG as BENCHMARK_INFO_TAG  # noqa: F401
from ddtrace_testing.internal.pytest.benchmark import (
    METRICS_TO_PYTEST_BENCHMARK_ATTRS as METRICS_TO_PYTEST_BENCHMARK_ATTRS,
)  # noqa: F401
from ddtrace_testing.internal.pytest.benchmark import (
    TAGS_TO_PYTEST_BENCHMARK_ATTRS as TAGS_TO_PYTEST_BENCHMARK_ATTRS,  # noqa: F401
)
from ddtrace_testing.internal.pytest.benchmark import BenchmarkData as BenchmarkData  # noqa: F401
from ddtrace_testing.internal.pytest.benchmark import (
    get_benchmark_tags_and_metrics as get_benchmark_tags_and_metrics,  # noqa: F401
)
