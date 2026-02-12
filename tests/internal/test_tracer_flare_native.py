"""
Tests for native tracer flare bindings.

These tests verify that the Rust-based tracer flare functionality is properly
exposed to Python and works as expected.
"""

import pytest


def _get_native_flare_or_skip():
    try:
        from ddtrace.internal.native._native import native_flare
    except ImportError as e:
        pytest.skip(f"Native tracer flare module not available: {e}")
    return native_flare


def test_import_native_module():
    """Verify that the native tracer flare module can be imported."""
    native_flare = _get_native_flare_or_skip()
    assert native_flare is not None


def test_create_tracer_flare_manager():
    """Test creating a TracerFlareManager instance."""
    native_flare = _get_native_flare_or_skip()
    manager = native_flare.TracerFlareManager(agent_url="http://localhost:8126", language="python")
    assert manager is not None
    assert "TracerFlareManager" in repr(manager)


def test_log_level_constants():
    """Test that LogLevel constants are available."""
    native_flare = _get_native_flare_or_skip()
    # Verify all log levels exist
    assert hasattr(native_flare.LogLevel, "TRACE")
    assert hasattr(native_flare.LogLevel, "DEBUG")
    assert hasattr(native_flare.LogLevel, "INFO")
    assert hasattr(native_flare.LogLevel, "WARN")
    assert hasattr(native_flare.LogLevel, "ERROR")
    assert hasattr(native_flare.LogLevel, "CRITICAL")
    assert hasattr(native_flare.LogLevel, "OFF")


def test_agent_task_file():
    """Test that AgentTaskFile is not exposed (internal only)."""
    native_flare = _get_native_flare_or_skip()
    # AgentTaskFile should NOT be accessible - it's internal only
    assert not hasattr(native_flare, "AgentTaskFile")


def test_exceptions_available():
    """Test that all exception types are available."""
    native_flare = _get_native_flare_or_skip()
    # Verify all exception types exist
    assert hasattr(native_flare, "ListeningError")
    assert hasattr(native_flare, "ParsingError")
    assert hasattr(native_flare, "SendError")
    assert hasattr(native_flare, "ZipError")
