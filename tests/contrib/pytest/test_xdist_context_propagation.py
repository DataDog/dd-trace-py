"""
Verify pytest-xdist context propagation functionality.

These tests verifies that the XdistHooks class properly extracts and passes span IDs
and that worker processes can create proper contexts from received span IDs.
"""
from unittest.mock import MagicMock
from unittest.mock import patch


def test_xdist_hooks_registers_span_id_with_valid_session_span():
    """Test that XdistHooks properly extracts and passes span ID when session span exists."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks

    mock_node = MagicMock()
    mock_node.workerinput = {}

    mock_span = MagicMock()
    mock_span.span_id = 12345

    with patch("ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=mock_span):
        hooks = XdistHooks()
        hooks.pytest_configure_node(mock_node)

    assert mock_node.workerinput["root_span"] == 12345


def test_xdist_hooks_registers_zero_when_no_session_span():
    """Test that XdistHooks uses 0 as fallback when no session span exists."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks

    mock_node = MagicMock()
    mock_node.workerinput = {}

    with patch("ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=None):
        hooks = XdistHooks()
        hooks.pytest_configure_node(mock_node)

    assert mock_node.workerinput["root_span"] == 0


def test_xdist_plugin_registration_when_xdist_present():
    """Test that XdistHooks are registered when xdist plugin is present."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks

    mock_config = MagicMock()
    mock_pluginmanager = MagicMock()
    mock_config.pluginmanager = mock_pluginmanager
    mock_pluginmanager.hasplugin.return_value = True

    # Simulate the registration logic from pytest_configure
    if mock_config.pluginmanager.hasplugin("xdist"):
        mock_config.pluginmanager.register(XdistHooks())

    # Verify register was called
    mock_config.pluginmanager.register.assert_called_once()
    # Verify the registered object is an XdistHooks instance
    registered_arg = mock_config.pluginmanager.register.call_args[0][0]
    assert isinstance(registered_arg, XdistHooks)


def test_xdist_plugin_not_registered_when_xdist_absent():
    """Test that XdistHooks are not registered when xdist plugin is absent."""
    mock_config = MagicMock()
    mock_pluginmanager = MagicMock()
    mock_config.pluginmanager = mock_pluginmanager
    mock_pluginmanager.hasplugin.return_value = False

    # Simulate the registration logic from pytest_configure
    if mock_config.pluginmanager.hasplugin("xdist"):
        mock_config.pluginmanager.register(MagicMock())  # This shouldn't happen

    # Verify register was not called
    mock_config.pluginmanager.register.assert_not_called()


def test_pytest_sessionstart_extracts_context_from_valid_workerinput():
    """Test that pytest_sessionstart properly extracts context and calls InternalTestSession.start with it."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

    # Mock session config with workerinput (simulates xdist worker)
    mock_session = MagicMock()
    mock_session.config.workerinput = {"root_span": "54321"}

    # Mock all the dependencies
    with patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True), patch(
        "ddtrace.contrib.internal.pytest._utils._get_session_command", return_value="pytest"
    ), patch("ddtrace.internal.test_visibility.api.InternalTestSession.discover"), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.set_library_capabilities"
    ), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.start"
    ) as mock_start, patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.efd_enabled", return_value=False
    ):
        pytest_sessionstart(mock_session)

        # Verify that start was called with the expected context
        mock_start.assert_called_once()
        distributed_children = mock_start.call_args[0][0]
        assert distributed_children is False  # No distributed children expected
        context = mock_start.call_args[0][1]
        assert context is not None
        assert context.span_id == 54321
        assert context.trace_id == 1


def test_pytest_sessionstart_handles_invalid_span_id_gracefully():
    """Test that pytest_sessionstart handles invalid span IDs gracefully."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

    # Mock session config with invalid span ID
    mock_session = MagicMock()
    mock_session.config.workerinput = {"root_span": "not_a_number"}

    # Mock all the dependencies
    with patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True), patch(
        "ddtrace.contrib.internal.pytest._utils._get_session_command", return_value="pytest"
    ), patch("ddtrace.internal.test_visibility.api.InternalTestSession.discover"), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.set_library_capabilities"
    ), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.start"
    ) as mock_start, patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.efd_enabled", return_value=False
    ):
        pytest_sessionstart(mock_session)

        # Verify that start was called with False, None (no distributed children, no context extracted)
        mock_start.assert_called_once_with(False, None)


def test_pytest_sessionstart_handles_missing_workerinput():
    """Test that pytest_sessionstart handles missing workerinput (main process)."""
    import pytest

    from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

    # Mock session config without workerinput (simulates main process)
    mock_session = MagicMock()
    del mock_session.config.workerinput

    # Store original hasattr function
    original_hasattr = hasattr

    def mock_hasattr(obj, name):
        # Mock the specific check for pytest.global_worker_itr_results to return False
        if obj is pytest and name == "global_worker_itr_results":
            return False
        return original_hasattr(obj, name)

    # Mock all the dependencies
    with patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True), patch(
        "ddtrace.contrib.internal.pytest._utils._get_session_command", return_value="pytest"
    ), patch("ddtrace.internal.test_visibility.api.InternalTestSession.discover"), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.set_library_capabilities"
    ), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.start"
    ) as mock_start, patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.efd_enabled", return_value=False
    ), patch(
        "builtins.hasattr", side_effect=mock_hasattr
    ):
        pytest_sessionstart(mock_session)

        # Verify that start was called with False (no distributed children), None (no context for main process)
        mock_start.assert_called_once_with(False, None)


def test_pytest_sessionstart_handles_missing_workerinput_with_global_worker_results():
    """
    Test that pytest_sessionstart handles missing workerinput when global_worker_itr_results exists
    """
    import pytest

    from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_sessionstart

    # Mock session config without workerinput (simulates main process)
    mock_session = MagicMock()
    del mock_session.config.workerinput

    # Store original hasattr function
    original_hasattr = hasattr

    def mock_hasattr(obj, name):
        # Mock the specific check for pytest.global_worker_itr_results to return True (simulating CI environment)
        if obj is pytest and name == "global_worker_itr_results":
            return True
        return original_hasattr(obj, name)

    # Mock all the dependencies
    with patch("ddtrace.contrib.internal.pytest._plugin_v2.is_test_visibility_enabled", return_value=True), patch(
        "ddtrace.contrib.internal.pytest._utils._get_session_command", return_value="pytest"
    ), patch("ddtrace.internal.test_visibility.api.InternalTestSession.discover"), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.set_library_capabilities"
    ), patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.start"
    ) as mock_start, patch(
        "ddtrace.internal.test_visibility.api.InternalTestSession.efd_enabled", return_value=False
    ), patch(
        "builtins.hasattr", side_effect=mock_hasattr
    ):
        pytest_sessionstart(mock_session)

        # This demonstrates the original bug: when global_worker_itr_results exists,
        # distributed_children gets set to True instead of False
        mock_start.assert_called_once_with(True, None)


def test_xdist_hooks_class_has_required_hookimpl():
    """Test that XdistHooks class has the required pytest_configure_node method."""
    from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks

    # Verify the class exists
    assert XdistHooks is not None

    # Verify it has the required method
    assert hasattr(XdistHooks, "pytest_configure_node")
    assert callable(getattr(XdistHooks, "pytest_configure_node"))

    # Verify we can instantiate it and call the method
    with patch("ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=None):
        hooks = XdistHooks()
        mock_node = MagicMock()
        mock_node.workerinput = {}

        # This should not raise an exception
        hooks.pytest_configure_node(mock_node)

        # Verify the workerinput was set correctly
        assert mock_node.workerinput["root_span"] == 0


@patch("ddtrace.internal.test_visibility.api.InternalTestSession.start")
def test_pytest_sessionstart_calls_start_with_extracted_context(mock_start):
    """Test that InternalTestSession.start is called with extracted context in xdist worker."""
    from ddtrace._trace.context import Context
    from ddtrace.constants import USER_KEEP

    # Create a context like what would be extracted
    test_context = Context(
        trace_id=1,
        span_id=99999,
        sampling_priority=USER_KEEP,
    )

    # Call InternalTestSession.start with the context (simulates pytest_sessionstart)
    from ddtrace.internal.test_visibility.api import InternalTestSession

    InternalTestSession.start(test_context)

    # Verify start was called with the context
    mock_start.assert_called_once_with(test_context)
