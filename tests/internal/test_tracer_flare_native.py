"""
Tests for native tracer flare bindings.

These tests verify that the Rust-based tracer flare functionality is properly
exposed to Python and works as expected.
"""

import pytest


class TestTracerFlareNativeBindings:
    """Test the native Rust bindings for tracer flare"""

    def test_import_native_module(self):
        """Verify that the native tracer flare module can be imported"""
        try:
            from ddtrace.internal.native._native import native_flare

            assert native_flare is not None
        except ImportError as e:
            pytest.skip(f"Native tracer flare module not available: {e}")

    def test_create_tracer_flare_manager(self):
        """Test creating a TracerFlareManager instance"""
        try:
            from ddtrace.internal.native._native import native_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        manager = native_flare.TracerFlareManager(agent_url="http://localhost:8126", language="python")
        assert manager is not None
        assert "TracerFlareManager" in repr(manager)

    def test_log_level_constants(self):
        """Test that LogLevel constants are available"""
        try:
            from ddtrace.internal.native._native import native_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Verify all log levels exist
        assert hasattr(native_flare.LogLevel, "TRACE")
        assert hasattr(native_flare.LogLevel, "DEBUG")
        assert hasattr(native_flare.LogLevel, "INFO")
        assert hasattr(native_flare.LogLevel, "WARN")
        assert hasattr(native_flare.LogLevel, "ERROR")
        assert hasattr(native_flare.LogLevel, "CRITICAL")
        assert hasattr(native_flare.LogLevel, "OFF")

    def test_agent_task_file(self):
        """Test that AgentTaskFile is not exposed (internal only)"""
        try:
            from ddtrace.internal.native._native import native_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # AgentTaskFile should NOT be accessible - it's internal only
        assert not hasattr(native_flare, "AgentTaskFile")

    def test_exceptions_available(self):
        """Test that all exception types are available"""
        try:
            from ddtrace.internal.native._native import native_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Verify all exception types exist
        assert hasattr(native_flare, "ListeningError")
        assert hasattr(native_flare, "ParsingError")
        assert hasattr(native_flare, "SendError")
        assert hasattr(native_flare, "ZipError")
