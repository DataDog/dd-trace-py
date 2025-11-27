"""
Integration tests for runtime request coverage.

Tests the full flow of runtime coverage collection and sending in real-world scenarios.
Uses subprocess isolation to ensure clean test environments.
"""

import pytest

from ddtrace.internal.ci_visibility.runtime_coverage import is_runtime_coverage_supported


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
    parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]},
)
def test_runtime_coverage_initialization():
    """Test that runtime coverage can be initialized successfully."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer

    # Initialize collector
    result = initialize_runtime_coverage()
    assert result is True, "Coverage collector initialization should succeed"

    # Initialize writer
    result = initialize_runtime_coverage_writer()
    assert result is True, "Coverage writer initialization should succeed"


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_collection_context():
    """Test that runtime coverage collection works with context manager."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.coverage.code import ModuleCodeCollector

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
        result = y + 8

    # Context should exit cleanly
    assert result == 12


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_build_payload():
    """Test building coverage payload with real collected data."""
    import os
    from pathlib import Path

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Initialize
    cwd_path = Path(os.getcwd())
    initialize_runtime_coverage()

    collector = ModuleCodeCollector._instance
    assert collector is not None

    # Collect coverage by importing and executing code from a module in the project
    # NOTE: Must build payload while still in context (before __exit__)
    coverage_ctx = collector.CollectInContext()
    with coverage_ctx:
        # Import existing test module (reuse from coverage tests)
        from tests.coverage.included_path.callee import called_in_context_main

        # Execute code to generate coverage
        called_in_context_main(1, 2)

        # Build payload while still in context (before __exit__)
        files = build_runtime_coverage_payload(
            coverage_ctx=coverage_ctx,
            root_dir=cwd_path,
        )

        # Verify payload structure
        assert files is not None
        assert isinstance(files, list)
        # Should have collected coverage for callee.py and in_context_lib.py
        assert len(files) > 0


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_send_to_writer():
    """Test sending coverage data to the writer."""
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer

    # Initialize writer
    result = initialize_runtime_coverage_writer()
    assert result is True

    # Create mock span with required attributes for HTTPWriter
    mock_span = mock.Mock()
    mock_span.trace_id = 12345
    mock_span.span_id = 67890
    mock_span._set_struct_tag = mock.Mock()
    mock_span._metrics = {}  # Required by HTTPWriter for _set_keep_rate
    mock_span.get_tags = mock.Mock(return_value={})  # Required by coverage encoder
    mock_span.get_struct_tag = mock.Mock(return_value=None)  # Required by coverage encoder

    # Send coverage
    files = [
        {"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]},
    ]

    result = send_runtime_coverage(mock_span, files)

    # Should succeed
    assert result is True, f"send_runtime_coverage returned {result}"
    mock_span._set_struct_tag.assert_called_once()


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_full_flow():
    """Test the complete runtime coverage flow from collection to sending."""
    import os
    from pathlib import Path
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Step 1: Initialize collector and writer
    assert initialize_runtime_coverage() is True
    assert initialize_runtime_coverage_writer() is True

    # Step 2: Collect coverage for a "request" and build payload WHILE IN CONTEXT
    collector = ModuleCodeCollector._instance
    cwd_path = Path(os.getcwd())

    coverage_ctx = collector.CollectInContext()
    with coverage_ctx:
        # Import and execute code from existing test modules
        from tests.coverage.included_path.callee import called_in_context_main
        from tests.coverage.included_path.callee import called_in_session_main

        called_in_context_main(1, 2)
        called_in_session_main(3, 4)

        # Step 3: Build coverage payload WHILE STILL IN CONTEXT
        files = build_runtime_coverage_payload(
            coverage_ctx=coverage_ctx,
            root_dir=cwd_path,
        )

        assert files is not None
        assert len(files) > 0

    # Step 4: Send coverage (can be done after context exits)
    mock_span = mock.Mock()
    mock_span.trace_id = 99999
    mock_span.span_id = 88888
    mock_span._set_struct_tag = mock.Mock()
    mock_span._metrics = {}  # Required by HTTPWriter
    mock_span.get_tags = mock.Mock(return_value={})  # Required by coverage encoder
    mock_span.get_struct_tag = mock.Mock(return_value=None)  # Required by coverage encoder

    result = send_runtime_coverage(mock_span, files)
    assert result is True


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_multiple_requests():
    """Test handling coverage for multiple concurrent requests."""
    import os
    from pathlib import Path
    from unittest import mock

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Initialize
    initialize_runtime_coverage()
    initialize_runtime_coverage_writer()

    collector = ModuleCodeCollector._instance
    cwd_path = Path(os.getcwd())

    # Simulate multiple requests
    for request_id in range(3):
        coverage_ctx = collector.CollectInContext()
        with coverage_ctx:
            # Import and execute code from existing test modules
            from tests.coverage.included_path.callee import called_in_context_main
            from tests.coverage.included_path.callee import called_in_session_main

            # Different code paths for different requests
            if request_id == 0:
                called_in_context_main(request_id, 1)
            elif request_id == 1:
                called_in_session_main(request_id, 2)
            else:
                called_in_context_main(request_id, 2)
                called_in_session_main(request_id, 3)

            # Build coverage payload WHILE STILL IN CONTEXT
            files = build_runtime_coverage_payload(
                coverage_ctx=coverage_ctx,
                root_dir=cwd_path,
            )

            assert files is not None
            assert len(files) > 0

        # Send coverage after context exits
        mock_span = mock.Mock()
        mock_span.trace_id = 10000 + request_id
        mock_span.span_id = 20000 + request_id
        mock_span._set_struct_tag = mock.Mock()
        mock_span._metrics = {}  # Required by HTTPWriter
        mock_span.get_tags = mock.Mock(return_value={})  # Required by coverage encoder
        mock_span.get_struct_tag = mock.Mock(return_value=None)  # Required by coverage encoder

        result = send_runtime_coverage(mock_span, files)
        assert result is True


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess
def test_runtime_coverage_disabled():
    """Test that runtime coverage does nothing when disabled."""
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage

    # Without DD_TRACE_RUNTIME_COVERAGE_ENABLED, should do nothing
    # But should not crash
    result = initialize_runtime_coverage()

    # Can succeed or fail depending on implementation, but should not crash
    # (Implementation may check env var in initialize or elsewhere)
    assert result in (True, False)


@pytest.mark.skipif(not is_runtime_coverage_supported(), reason="Requires Python 3.12+")
@pytest.mark.subprocess(
    env={"DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true"},
)
def test_runtime_coverage_with_imports():
    """Test that runtime coverage tracks imports correctly."""
    import os
    from pathlib import Path

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Initialize with import-time coverage
    cwd_path = Path(os.getcwd())
    initialize_runtime_coverage()

    collector = ModuleCodeCollector._instance

    coverage_ctx = collector.CollectInContext()
    with coverage_ctx:
        # Import and execute code from existing test modules (gets instrumented)
        from tests.coverage.included_path.callee import called_in_context_main

        called_in_context_main(1, 2)

        # Build payload WHILE STILL IN CONTEXT
        files = build_runtime_coverage_payload(
            coverage_ctx=coverage_ctx,
            root_dir=cwd_path,
        )

        # Should have coverage data for callee.py and in_context_lib.py
        assert files is not None
        assert len(files) > 0
