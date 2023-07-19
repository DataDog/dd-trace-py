import pytest

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS


@pytest.fixture
def mock_telemetry_lifecycle_writer():
    telemetry_writer.disable()
    telemetry_writer.enable()
    metrics_result = telemetry_writer._namespace._metrics_data
    assert len(metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    assert len(metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    yield telemetry_writer
    telemetry_writer._namespace.flush()


@pytest.fixture
def mock_logs_telemetry_lifecycle_writer():
    telemetry_writer.disable()
    telemetry_writer.enable()
    metrics_result = telemetry_writer._logs
    assert len(metrics_result) == 0
    yield telemetry_writer
    telemetry_writer.reset_queues()
