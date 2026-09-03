"""
Tests for automatic source:otel tag on evaluations when OTel tracing is enabled.

When DD_TRACE_OTEL_ENABLED=true, all evaluations should have `source:otel` tag
to allow the backend to wait for OTel span conversion (~3 minutes) before
discarding unmatched evaluations.
"""

import mock


def test_submit_evaluation_adds_source_otel_when_otel_enabled(llmobs, mock_llmobs_eval_metric_writer):
    """Verify source:otel tag is auto-added when DD_TRACE_OTEL_ENABLED=true."""
    with mock.patch("ddtrace.llmobs._llmobs.config._otel_trace_enabled", True):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="quality",
            metric_type="score",
            value=0.9,
            ml_app="test-app",
        )

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once()
    call_args = mock_llmobs_eval_metric_writer.enqueue.call_args[0][0]

    assert "tags" in call_args
    assert "source:otel" in call_args["tags"]


def test_submit_evaluation_no_source_otel_when_otel_disabled(llmobs, mock_llmobs_eval_metric_writer):
    """Verify source:otel tag is NOT added when DD_TRACE_OTEL_ENABLED=false (default)."""
    with mock.patch("ddtrace.llmobs._llmobs.config._otel_trace_enabled", False):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="quality",
            metric_type="score",
            value=0.9,
            ml_app="test-app",
        )

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once()
    call_args = mock_llmobs_eval_metric_writer.enqueue.call_args[0][0]

    assert "tags" in call_args
    assert "source:otel" not in call_args["tags"]
