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
            from ddtrace.internal.native._native import register_tracer_flare

            assert register_tracer_flare is not None
        except ImportError as e:
            pytest.skip(f"Native tracer flare module not available: {e}")

    def test_create_tracer_flare_manager(self):
        """Test creating a TracerFlareManager instance"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        manager = register_tracer_flare.TracerFlareManager(agent_url="http://localhost:8126", language="python")
        assert manager is not None
        assert "TracerFlareManager" in repr(manager)

    def test_log_level_constants(self):
        """Test that LogLevel constants are available"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Verify all log levels exist
        assert hasattr(register_tracer_flare.LogLevel, "TRACE")
        assert hasattr(register_tracer_flare.LogLevel, "DEBUG")
        assert hasattr(register_tracer_flare.LogLevel, "INFO")
        assert hasattr(register_tracer_flare.LogLevel, "WARN")
        assert hasattr(register_tracer_flare.LogLevel, "ERROR")
        assert hasattr(register_tracer_flare.LogLevel, "CRITICAL")
        assert hasattr(register_tracer_flare.LogLevel, "OFF")

    def test_return_action_static_constructors(self):
        """Test that ReturnAction static constructors work"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Test ReturnAction.none()
        none_action = register_tracer_flare.ReturnAction.none()
        assert none_action.is_none() is True
        assert none_action.is_send() is False
        assert none_action.is_set() is False
        assert none_action.is_unset() is False

        # Test ReturnAction.unset()
        unset_action = register_tracer_flare.ReturnAction.unset()
        assert unset_action.is_unset() is True
        assert unset_action.is_none() is False

        # Test ReturnAction.send()
        task = register_tracer_flare.AgentTaskFile(
            case_id=123456,
            hostname="test-host",
            user_handle="test@example.com",
            task_type="tracer_flare",
            uuid="test-uuid-123",
        )
        send_action = register_tracer_flare.ReturnAction.send(task)
        assert send_action.is_send() is True
        assert send_action.is_none() is False

    def test_agent_task_file(self):
        """Test AgentTaskFile structure"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # AgentTaskFile should be accessible as a class
        assert hasattr(register_tracer_flare, "AgentTaskFile")

    def test_exceptions_available(self):
        """Test that all exception types are available"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Verify all exception types exist
        assert hasattr(register_tracer_flare, "ListeningError")
        assert hasattr(register_tracer_flare, "ParsingError")
        assert hasattr(register_tracer_flare, "SendError")
        assert hasattr(register_tracer_flare, "ZipError")

    def test_agent_task_file_creation(self):
        """Test creating AgentTaskFile instances"""
        try:
            from ddtrace.internal.native._native import register_tracer_flare
        except ImportError:
            pytest.skip("Native tracer flare module not available")

        # Create an AgentTaskFile
        task = register_tracer_flare.AgentTaskFile(
            case_id=123456,
            hostname="test-host",
            user_handle="user@example.com",
            task_type="tracer_flare",
            uuid="test-uuid-123",
        )

        # Verify attributes are accessible
        assert task.case_id == 123456
        assert task.hostname == "test-host"
        assert task.user_handle == "user@example.com"
        assert task.task_type == "tracer_flare"
        assert task.uuid == "test-uuid-123"
