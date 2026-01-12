"""Unit tests for SCA Remote Configuration handler."""

import mock

from ddtrace.appsec.sca._remote_config import SCADetectionRC
from ddtrace.appsec.sca._remote_config import _sca_detection_callback
from ddtrace.appsec.sca._remote_config import disable_sca_detection_rc
from ddtrace.appsec.sca._remote_config import enable_sca_detection_rc


def test_sca_detection_rc_creation():
    """Test creating SCADetectionRC instance."""

    def dummy_callback(payload_list):
        pass

    sca_rc = SCADetectionRC(dummy_callback)

    assert sca_rc is not None
    assert sca_rc._publisher is not None
    assert sca_rc._subscriber is not None


def test_sca_detection_callback_empty_list():
    """Test callback with empty payload list."""
    # Should not raise
    _sca_detection_callback([])


def test_sca_detection_callback_add_targets():
    """Test callback processing target additions."""

    # Create a mock payload with targets to add
    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join", "os.path:exists"]}
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        payload_list = [MockPayload()]
        _sca_detection_callback(payload_list)

        # Should call apply_instrumentation_updates with correct args
        mock_apply.assert_called_once()
        args, kwargs = mock_apply.call_args
        targets_to_add, targets_to_remove = args

        assert "os.path:join" in targets_to_add
        assert "os.path:exists" in targets_to_add
        assert len(targets_to_remove) == 0


def test_sca_detection_callback_remove_targets():
    """Test callback processing target removals."""

    # Create a mock payload for deletion (content=None)
    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = None  # None means deletion
            self.metadata = {"targets": ["os.path:join", "os.path:exists"]}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        payload_list = [MockPayload()]
        _sca_detection_callback(payload_list)

        # Should call apply_instrumentation_updates with correct args
        mock_apply.assert_called_once()
        args, kwargs = mock_apply.call_args
        targets_to_add, targets_to_remove = args

        assert len(targets_to_add) == 0
        assert "os.path:join" in targets_to_remove
        assert "os.path:exists" in targets_to_remove


def test_sca_detection_callback_mixed_operations():
    """Test callback with both additions and removals."""

    class MockPayloadAdd:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    class MockPayloadRemove:
        def __init__(self):
            self.path = "sca/detection/config2"
            self.content = None
            self.metadata = {"targets": ["os.path:exists"]}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        payload_list = [MockPayloadAdd(), MockPayloadRemove()]
        _sca_detection_callback(payload_list)

        mock_apply.assert_called_once()
        args, kwargs = mock_apply.call_args
        targets_to_add, targets_to_remove = args

        assert "os.path:join" in targets_to_add
        assert "os.path:exists" in targets_to_remove


def test_sca_detection_callback_invalid_content():
    """Test callback handles invalid content gracefully."""

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = "not a dict"  # Invalid - should be dict
            self.metadata = {}

    # Should not raise, just log warning
    _sca_detection_callback([MockPayload()])


def test_sca_detection_callback_invalid_targets_format():
    """Test callback handles invalid targets format gracefully."""

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": "not a list"}  # Invalid - should be list
            self.metadata = {}

    # Should not raise, just log warning
    _sca_detection_callback([MockPayload()])


def test_sca_detection_callback_missing_targets():
    """Test callback handles missing targets key gracefully."""

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"other_key": "value"}  # Missing 'targets' key
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        _sca_detection_callback([MockPayload()])

        # Should not call apply_instrumentation_updates (no targets)
        mock_apply.assert_not_called()


def test_sca_detection_callback_handles_exception():
    """Test callback handles exceptions in apply_instrumentation_updates gracefully."""

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        mock_apply.side_effect = RuntimeError("Test error")

        # Should not raise - exception should be caught and logged
        _sca_detection_callback([MockPayload()])


def test_enable_sca_detection_rc():
    """Test enabling SCA detection RC."""
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller") as mock_poller:
        enable_sca_detection_rc()

        # Should register with RC poller
        mock_poller.register.assert_called_once()
        args, kwargs = mock_poller.register.call_args

        # Check product name
        assert args[0] == "SCA_DETECTION"

        # Check PubSub instance
        assert isinstance(args[1], SCADetectionRC)

        # Check restart_on_fork
        assert kwargs.get("restart_on_fork") is True


def test_disable_sca_detection_rc():
    """Test disabling SCA detection RC."""
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller") as mock_poller:
        disable_sca_detection_rc()

        # Should unregister from RC poller
        mock_poller.unregister.assert_called_once_with("SCA_DETECTION")


def test_sca_detection_callback_multiple_targets():
    """Test callback with multiple targets in single payload."""

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {
                "targets": [
                    "module1:func1",
                    "module2:Class.method",
                    "module3:func3",
                    "module4:Class.static_method",
                ]
            }
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        _sca_detection_callback([MockPayload()])

        mock_apply.assert_called_once()
        args, kwargs = mock_apply.call_args
        targets_to_add, targets_to_remove = args

        assert len(targets_to_add) == 4
        assert "module1:func1" in targets_to_add
        assert "module2:Class.method" in targets_to_add
        assert "module3:func3" in targets_to_add
        assert "module4:Class.static_method" in targets_to_add


def test_sca_detection_callback_multiple_payloads():
    """Test callback with multiple payloads."""

    class MockPayload1:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["module1:func1", "module2:func2"]}
            self.metadata = {}

    class MockPayload2:
        def __init__(self):
            self.path = "sca/detection/config2"
            self.content = {"targets": ["module3:func3"]}
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.apply_instrumentation_updates") as mock_apply:
        payload_list = [MockPayload1(), MockPayload2()]
        _sca_detection_callback(payload_list)

        mock_apply.assert_called_once()
        args, kwargs = mock_apply.call_args
        targets_to_add, targets_to_remove = args

        # Should aggregate all targets
        assert len(targets_to_add) == 3
        assert "module1:func1" in targets_to_add
        assert "module2:func2" in targets_to_add
        assert "module3:func3" in targets_to_add
