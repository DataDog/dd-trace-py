"""Unit tests for Enrichment Task 10: ray.job close-time tags from JobInfo.

These tests verify that ``RaySpanManager._finish_span`` stamps the extra
``JobInfo`` fields (start_time, end_time, driver_node_id,
driver_agent_http_address, runtime_env) on the ``ray.job`` span at close
time — without requiring a live Ray cluster.
"""

from ddtrace.contrib.internal.ray.constants import RAY_JOB_DRIVER_AGENT_HTTP_ADDRESS
from ddtrace.contrib.internal.ray.constants import RAY_JOB_DRIVER_NODE_ID
from ddtrace.contrib.internal.ray.constants import RAY_JOB_END_TIME_MS
from ddtrace.contrib.internal.ray.constants import RAY_JOB_HAS_RUNTIME_ENV
from ddtrace.contrib.internal.ray.constants import RAY_JOB_MESSAGE
from ddtrace.contrib.internal.ray.constants import RAY_JOB_START_TIME_MS
from ddtrace.contrib.internal.ray.constants import RAY_JOB_STATUS
from ddtrace.contrib.internal.ray.span_manager import RaySpanManager
from ddtrace.trace import tracer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeJobInfo:
    """Minimal stand-in for ``ray.dashboard.modules.job.common.JobInfo``."""

    status = "SUCCEEDED"
    message = "Job finished successfully."
    start_time = 1_700_000_000_000
    end_time = 1_700_001_000_000
    driver_node_id = "node-abc123"
    driver_agent_http_address = "http://10.0.0.1:52365"
    runtime_env = {"pip": ["torch==2.0.0"]}


def _make_span(submission_id="sub-1"):
    span = tracer.start_span("ray.job", service="test-svc")
    span._set_attribute("ray.submission_id", submission_id)
    return span


# ---------------------------------------------------------------------------
# Tests: full JobInfo with all fields present
# ---------------------------------------------------------------------------


def test_finish_span_stamps_start_time():
    """start_time from JobInfo lands as a numeric span attribute."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_metric(RAY_JOB_START_TIME_MS) == 1_700_000_000_000


def test_finish_span_stamps_end_time():
    """end_time from JobInfo lands as a numeric span attribute."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_metric(RAY_JOB_END_TIME_MS) == 1_700_001_000_000


def test_finish_span_stamps_driver_node_id():
    """driver_node_id from JobInfo lands as a string span tag."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_tag(RAY_JOB_DRIVER_NODE_ID) == "node-abc123"


def test_finish_span_stamps_driver_agent_http_address():
    """driver_agent_http_address from JobInfo lands as a string span tag."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_tag(RAY_JOB_DRIVER_AGENT_HTTP_ADDRESS) == "http://10.0.0.1:52365"


def test_finish_span_stamps_has_runtime_env_true_when_present():
    """has_runtime_env is 'true' when runtime_env is a non-empty dict."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_tag(RAY_JOB_HAS_RUNTIME_ENV) == "true"


def test_finish_span_also_sets_status_and_message():
    """Existing status and message tags are still set alongside the new ones."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfo())

    assert span.get_tag(RAY_JOB_STATUS) == "SUCCEEDED"
    assert span.get_tag(RAY_JOB_MESSAGE) == "Job finished successfully."


# ---------------------------------------------------------------------------
# Tests: optional fields missing (older Ray versions)
# ---------------------------------------------------------------------------


class _FakeJobInfoMinimal:
    """JobInfo without the optional new fields — simulates an older Ray release."""

    status = "SUCCEEDED"
    message = "ok"
    # start_time, end_time, driver_node_id, driver_agent_http_address, runtime_env
    # are intentionally absent.


def test_finish_span_tolerates_missing_start_time():
    """Missing start_time does not raise and no tag is emitted."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoMinimal())

    assert span.get_metric(RAY_JOB_START_TIME_MS) is None


def test_finish_span_tolerates_missing_end_time():
    """Missing end_time does not raise and no tag is emitted."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoMinimal())

    assert span.get_metric(RAY_JOB_END_TIME_MS) is None


def test_finish_span_tolerates_missing_driver_node_id():
    """Missing driver_node_id does not raise and no tag is emitted."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoMinimal())

    assert span.get_tag(RAY_JOB_DRIVER_NODE_ID) is None


def test_finish_span_tolerates_missing_driver_agent_http_address():
    """Missing driver_agent_http_address does not raise and no tag is emitted."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoMinimal())

    assert span.get_tag(RAY_JOB_DRIVER_AGENT_HTTP_ADDRESS) is None


def test_finish_span_stamps_has_runtime_env_false_when_missing():
    """has_runtime_env is 'false' when runtime_env is absent (getattr returns None)."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoMinimal())

    assert span.get_tag(RAY_JOB_HAS_RUNTIME_ENV) == "false"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


def test_finish_span_no_job_info_does_not_stamp_new_tags():
    """When job_info is None (non-job spans), none of the new tags appear."""
    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=None)

    assert span.get_metric(RAY_JOB_START_TIME_MS) is None
    assert span.get_metric(RAY_JOB_END_TIME_MS) is None
    assert span.get_tag(RAY_JOB_DRIVER_NODE_ID) is None
    assert span.get_tag(RAY_JOB_DRIVER_AGENT_HTTP_ADDRESS) is None
    assert span.get_tag(RAY_JOB_HAS_RUNTIME_ENV) is None


def test_finish_span_has_runtime_env_false_for_empty_dict():
    """has_runtime_env is 'false' when runtime_env is an empty dict (falsy)."""

    class _FakeJobInfoEmptyEnv:
        status = "SUCCEEDED"
        message = "ok"
        start_time = None
        end_time = None
        driver_node_id = None
        driver_agent_http_address = None
        runtime_env = {}

    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoEmptyEnv())

    assert span.get_tag(RAY_JOB_HAS_RUNTIME_ENV) == "false"


def test_finish_span_start_end_time_as_strings():
    """start_time / end_time provided as strings are coerced to int."""

    class _FakeJobInfoStringTimes:
        status = "SUCCEEDED"
        message = "ok"
        start_time = "1700000000000"
        end_time = "1700001000000"
        driver_node_id = None
        driver_agent_http_address = None
        runtime_env = None

    span = _make_span()
    manager = RaySpanManager()
    manager._finish_span(span, job_info=_FakeJobInfoStringTimes())

    assert span.get_metric(RAY_JOB_START_TIME_MS) == 1_700_000_000_000
    assert span.get_metric(RAY_JOB_END_TIME_MS) == 1_700_001_000_000
