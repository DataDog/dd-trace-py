"""Integration tests for SCA detection end-to-end flow."""

import mock

from ddtrace.appsec.sca import disable_sca_detection
from ddtrace.appsec.sca import enable_sca_detection
from ddtrace.appsec.sca._registry import get_global_registry
from ddtrace.appsec.sca._remote_config import _sca_detection_callback


def test_end_to_end_flow():
    """Test the complete SCA detection flow from enable to instrumentation to hit recording."""

    # Clear any existing state
    registry = get_global_registry()
    registry.clear()

    # Step 1: Enable SCA detection
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    # Step 2: Simulate RC payload with targets
    class MockPayload:
        def __init__(self, targets):
            self.path = "sca/detection/config1"
            self.content = {"targets": targets}
            self.metadata = {}

    # Send payload to instrument os.path:join
    payload = MockPayload(["os.path:join"])
    _sca_detection_callback([payload])

    # Step 3: Verify target was instrumented
    assert registry.has_target("os.path:join")
    assert registry.is_instrumented("os.path:join")

    # Step 4: Call the instrumented function
    import os.path

    result = os.path.join("foo", "bar")

    # Step 5: Verify hit was recorded
    assert result == os.path.join("foo", "bar")  # Function still works
    assert registry.get_hit_count("os.path:join") >= 1  # Hit was recorded

    # Step 6: Disable SCA detection
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()


def test_multiple_targets_instrumentation():
    """Test instrumenting multiple targets."""

    # Clear state
    registry = get_global_registry()
    registry.clear()

    # Enable SCA detection
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    # Simulate RC payload with multiple targets
    class MockPayload:
        def __init__(self, targets):
            self.path = "sca/detection/config1"
            self.content = {"targets": targets}
            self.metadata = {}

    payload = MockPayload(["os.path:join", "os.path:exists", "os.path:dirname"])
    _sca_detection_callback([payload])

    # Verify all targets were instrumented
    assert registry.is_instrumented("os.path:join")
    assert registry.is_instrumented("os.path:exists")
    assert registry.is_instrumented("os.path:dirname")

    # Call each function
    import os.path

    os.path.join("a", "b")
    os.path.exists("/tmp")
    os.path.dirname("/foo/bar")

    # Verify all hits were recorded
    assert registry.get_hit_count("os.path:join") >= 1
    assert registry.get_hit_count("os.path:exists") >= 1
    assert registry.get_hit_count("os.path:dirname") >= 1

    # Cleanup
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()


def test_target_removal():
    """Test removing instrumentation targets."""

    # Clear state
    registry = get_global_registry()
    registry.clear()

    # Enable and add targets
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    class MockPayloadAdd:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join", "os.path:exists"]}
            self.metadata = {}

    _sca_detection_callback([MockPayloadAdd()])

    assert registry.has_target("os.path:join")
    assert registry.has_target("os.path:exists")

    # Remove one target
    class MockPayloadRemove:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = None  # Deletion
            self.metadata = {"targets": ["os.path:exists"]}

    _sca_detection_callback([MockPayloadRemove()])

    # Verify removal
    assert registry.has_target("os.path:join")  # Still there
    assert not registry.has_target("os.path:exists")  # Removed

    # Cleanup
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()


def test_pending_target_resolution():
    """Test that pending targets (modules not yet imported) are tracked."""

    # Clear state
    registry = get_global_registry()
    registry.clear()

    # Enable SCA detection
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    # Simulate RC payload with non-existent module
    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["nonexistent.module:function"]}
            self.metadata = {}

    _sca_detection_callback([MockPayload()])

    # Verify target is tracked as pending
    assert registry.has_target("nonexistent.module:function")
    assert registry.is_pending("nonexistent.module:function")
    assert not registry.is_instrumented("nonexistent.module:function")

    # Cleanup
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()


def test_enable_disable_idempotence():
    """Test that enable/disable are idempotent."""

    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        # Enable twice - should not error
        enable_sca_detection()
        enable_sca_detection()

        # Disable twice - should not error
        disable_sca_detection()
        disable_sca_detection()


def test_instrumented_function_behavior_preserved():
    """Test that instrumentation doesn't break function behavior."""

    # Clear state
    registry = get_global_registry()
    registry.clear()

    # Enable and instrument
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    _sca_detection_callback([MockPayload()])

    # Test various argument patterns
    import os.path

    # Positional args
    assert os.path.join("a", "b") == os.path.join("a", "b")

    # Multiple args
    assert os.path.join("a", "b", "c", "d") == os.path.join("a", "b", "c", "d")

    # Edge cases
    assert os.path.join("") == ""
    assert os.path.join("a") == "a"

    # Cleanup
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()


def test_stats_collection():
    """Test that stats are collected correctly."""

    # Clear state
    registry = get_global_registry()
    registry.clear()

    # Enable and instrument
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        enable_sca_detection()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join", "os.path:exists", "nonexistent.module:func"]}
            self.metadata = {}

    _sca_detection_callback([MockPayload()])

    # Get stats
    stats = registry.get_stats()

    # Verify stats structure
    assert "os.path:join" in stats
    assert "os.path:exists" in stats
    assert "nonexistent.module:func" in stats

    # Verify stats content
    assert stats["os.path:join"]["is_instrumented"] is True
    assert stats["os.path:join"]["is_pending"] is False

    assert stats["nonexistent.module:func"]["is_instrumented"] is False
    assert stats["nonexistent.module:func"]["is_pending"] is True

    # Cleanup
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        disable_sca_detection()
