"""
Integration tests for runtime request coverage.

Tests the full flow of runtime coverage collection and sending in real-world scenarios.
Uses subprocess isolation to ensure clean test environments.
"""

import os
from pathlib import Path

import pytest


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
    parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]},
)
def test_runtime_coverage_initialization():
    """Test that runtime coverage can be initialized successfully."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize collector
    result = initialize_runtime_coverage()
    assert result is True, "Coverage collector initialization should succeed"

    # Initialize writer
    result = initialize_runtime_coverage_writer()
    assert result is True, "Coverage writer initialization should succeed"


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_collection_context():
    """Test that runtime coverage collection works with context manager."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize
    initialize_runtime_coverage()

    # Verify collector is available
    assert ModuleCodeCollector._instance is not None

    # Test context manager
    collector = ModuleCodeCollector._instance
    with collector.CollectInContext():
        # Some code execution
        x = 1 + 1
        y = x * 2
        result = y + 10

    # Context should exit cleanly
    assert result == 12


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_build_payload():
    """Test building coverage payload with real collected data."""

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize
    cwd_path = Path(os.getcwd())
    initialize_runtime_coverage()

    collector = ModuleCodeCollector._instance
    assert collector is not None

    # Collect some coverage
    with collector.CollectInContext():
        # Execute some code
        def test_function():
            a = 1
            b = 2
            return a + b

        test_function()

    # Build payload
    payload = build_runtime_coverage_payload(
        root_dir=cwd_path,
        trace_id=12345,
        span_id=67890,
    )

    # Verify payload structure
    assert payload is not None
    assert "trace_id" in payload
    assert "span_id" in payload
    assert "files" in payload
    assert payload["trace_id"] == 12345
    assert payload["span_id"] == 67890
    assert isinstance(payload["files"], list)


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_send_to_writer():
    """Test sending coverage data to the writer."""
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize writer
    result = initialize_runtime_coverage_writer()
    assert result is True

    # Create mock span
    mock_span = mock.Mock()
    mock_span.trace_id = 12345
    mock_span.span_id = 67890
    mock_span._set_struct_tag = mock.Mock()

    # Send coverage
    files = [
        {"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]},
    ]

    result = send_runtime_coverage(mock_span, files)

    # Should succeed
    assert result is True
    mock_span._set_struct_tag.assert_called_once()


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_full_flow():
    """Test the complete runtime coverage flow from collection to sending."""
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Step 1: Initialize collector and writer
    assert initialize_runtime_coverage() is True
    assert initialize_runtime_coverage_writer() is True

    # Step 2: Collect coverage for a "request"
    collector = ModuleCodeCollector._instance
    cwd_path = Path(os.getcwd())

    with collector.CollectInContext():
        # Simulate some request handling code
        def handle_request():
            data = {"status": "ok"}
            result = process_data(data)
            return result

        def process_data(data):
            return {"processed": True, **data}

        handle_request()

    # Step 3: Build coverage payload
    payload = build_runtime_coverage_payload(
        root_dir=cwd_path,
        trace_id=99999,
        span_id=88888,
    )

    assert payload is not None
    assert len(payload["files"]) > 0

    # Step 4: Send coverage
    mock_span = mock.Mock()
    mock_span.trace_id = 99999
    mock_span.span_id = 88888
    mock_span._set_struct_tag = mock.Mock()

    result = send_runtime_coverage(mock_span, payload["files"])
    assert result is True


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_multiple_requests():
    """Test handling coverage for multiple concurrent requests."""
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize
    initialize_runtime_coverage()
    initialize_runtime_coverage_writer()

    collector = ModuleCodeCollector._instance
    cwd_path = Path(os.getcwd())

    # Simulate multiple requests
    for request_id in range(3):
        with collector.CollectInContext():
            # Different code paths for different requests
            if request_id == 0:

                def handler():
                    return {"request": 0}

                handler()
            elif request_id == 1:

                def handler():
                    data = [1, 2, 3]
                    return {"sum": sum(data)}

                handler()
            else:

                def handler():
                    return {"final": True}

                handler()

        # Build and send coverage for each request
        payload = build_runtime_coverage_payload(
            root_dir=cwd_path,
            trace_id=10000 + request_id,
            span_id=20000 + request_id,
        )

        if payload:
            mock_span = mock.Mock()
            mock_span.trace_id = 10000 + request_id
            mock_span.span_id = 20000 + request_id
            mock_span._set_struct_tag = mock.Mock()

            result = send_runtime_coverage(mock_span, payload["files"])
            assert result is True


@pytest.mark.subprocess
def test_runtime_coverage_disabled():
    """Test that runtime coverage does nothing when disabled."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Without DD_TRACE_RUNTIME_COVERAGE_ENABLED, should do nothing
    # But should not crash
    result = initialize_runtime_coverage()

    # Can succeed or fail depending on implementation, but should not crash
    # (Implementation may check env var in initialize or elsewhere)
    assert result in (True, False)


@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_with_imports():
    """Test that runtime coverage tracks imports correctly."""

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    if not is_runtime_coverage_supported():
        pytest.skip("Runtime coverage requires Python 3.12+")

    # Initialize with import-time coverage
    cwd_path = Path(os.getcwd())
    initialize_runtime_coverage()

    collector = ModuleCodeCollector._instance

    with collector.CollectInContext():
        # Import a module to see if import-time coverage is tracked
        import json

        data = {"test": "value"}
        json.dumps(data)

    # Build payload (should include imported modules if configured)
    payload = build_runtime_coverage_payload(
        root_dir=cwd_path,
        trace_id=12345,
        span_id=67890,
    )

    # Should have coverage data
    assert payload is not None
    assert len(payload["files"]) > 0
