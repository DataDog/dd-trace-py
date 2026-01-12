"""Tests for SCA detection observability (telemetry, span tags, metrics)."""

import mock

from ddtrace.appsec._constants import SCA
from ddtrace.appsec.sca import disable_sca_detection
from ddtrace.appsec.sca import enable_sca_detection
from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates
from ddtrace.appsec.sca._registry import get_global_registry
from ddtrace.appsec.sca._remote_config import _sca_detection_callback
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


def test_enable_sca_detection_sends_telemetry():
    """Test that enabling SCA detection sends telemetry metrics."""
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        with mock.patch("ddtrace.appsec.sca.telemetry_writer") as mock_writer:
            enable_sca_detection()

            # Should send enabled metric
            mock_writer.add_gauge_metric.assert_called_with(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.enabled", 1)

        # Cleanup
        with mock.patch("ddtrace.appsec.sca.telemetry_writer"):
            disable_sca_detection()


def test_disable_sca_detection_sends_telemetry():
    """Test that disabling SCA detection sends telemetry metrics."""
    # First enable
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        with mock.patch("ddtrace.appsec.sca.telemetry_writer"):
            enable_sca_detection()

    # Then disable with telemetry check
    with mock.patch("ddtrace.appsec.sca._remote_config.remoteconfig_poller"):
        with mock.patch("ddtrace.appsec.sca.telemetry_writer") as mock_writer:
            disable_sca_detection()

            # Should send disabled metric
            mock_writer.add_gauge_metric.assert_called_with(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.enabled", 0)


def test_instrumentation_success_sends_telemetry():
    """Test that successful instrumentation sends telemetry."""
    registry = get_global_registry()
    registry.clear()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.telemetry_writer") as mock_writer:
        _sca_detection_callback([MockPayload()])

        # Should send success metric
        calls = [call for call in mock_writer.add_count_metric.call_args_list if "instrumentation_success" in str(call)]
        assert len(calls) == 1
        assert calls[0][0][0] == TELEMETRY_NAMESPACE.APPSEC
        assert calls[0][0][1] == "sca.detection.instrumentation_success"
        assert calls[0][0][2] == 1

    registry.clear()


def test_instrumentation_failure_sends_telemetry():
    """Test that failed instrumentation sends telemetry."""
    registry = get_global_registry()
    registry.clear()

    # Create a payload with a target that will fail to instrument
    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["nonexistent.module:function"]}
            self.metadata = {}

    # Mock inject_hook to raise an error
    with mock.patch("ddtrace.appsec.sca._instrumenter.inject_hook") as mock_inject:
        mock_inject.side_effect = RuntimeError("Injection failed")

        with mock.patch("ddtrace.appsec.sca._instrumenter.telemetry_writer") as mock_writer:
            # First add the target so it's resolvable (but injection will fail)
            with mock.patch("ddtrace.appsec.sca._resolver.SymbolResolver.resolve") as mock_resolve:

                def fake_func():
                    pass

                mock_resolve.return_value = ("test:func", fake_func)

                apply_instrumentation_updates(["test:func"], [])

                # Should send error metric
                calls = [
                    call
                    for call in mock_writer.add_count_metric.call_args_list
                    if "instrumentation_errors" in str(call)
                ]
                assert len(calls) == 1
                assert calls[0][0][0] == TELEMETRY_NAMESPACE.APPSEC
                assert calls[0][0][1] == "sca.detection.instrumentation_errors"
                assert calls[0][0][2] == 1

    registry.clear()


def test_instrumentation_updates_send_gauge_metrics():
    """Test that instrumentation updates send gauge metrics for state."""
    registry = get_global_registry()
    registry.clear()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join", "os.path:exists", "nonexistent.module:func"]}
            self.metadata = {}

    with mock.patch("ddtrace.appsec.sca._instrumenter.telemetry_writer") as mock_writer:
        _sca_detection_callback([MockPayload()])

        # Should send gauge metrics for targets
        gauge_calls = [
            call for call in mock_writer.add_gauge_metric.call_args_list if "sca.detection.targets" in str(call)
        ]

        # Should have 3 gauge metrics: total, instrumented, pending
        assert len(gauge_calls) >= 3

        # Check total targets metric
        total_calls = [call for call in gauge_calls if call[0][1] == "sca.detection.targets_total"]
        assert len(total_calls) == 1
        assert total_calls[0][0][2] == 3  # 3 total targets

        # Check instrumented metric
        instrumented_calls = [call for call in gauge_calls if call[0][1] == "sca.detection.targets_instrumented"]
        assert len(instrumented_calls) == 1
        assert instrumented_calls[0][0][2] == 2  # 2 instrumented (os.path:join, os.path:exists)

        # Check pending metric
        pending_calls = [call for call in gauge_calls if call[0][1] == "sca.detection.targets_pending"]
        assert len(pending_calls) == 1
        assert pending_calls[0][0][2] == 1  # 1 pending (nonexistent.module:func)

    registry.clear()


def test_hook_execution_sends_telemetry():
    """Test that hook execution sends telemetry metrics."""
    registry = get_global_registry()
    registry.clear()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    # Enable and instrument
    _sca_detection_callback([MockPayload()])

    # Call instrumented function with telemetry mock
    with mock.patch("ddtrace.appsec.sca._instrumenter.telemetry_writer") as mock_writer:
        import os.path

        os.path.join("foo", "bar")

        # Should send hook hit metric with target tag
        calls = [call for call in mock_writer.add_count_metric.call_args_list if "hook_hits" in str(call)]
        assert len(calls) >= 1
        assert calls[0][0][0] == TELEMETRY_NAMESPACE.APPSEC
        assert calls[0][0][1] == "sca.detection.hook_hits"
        assert calls[0][0][2] == 1

        # Check tags include target
        if len(calls[0]) > 1 and "tags" in calls[0][1]:
            tags = calls[0][1]["tags"]
            assert ("target", "os.path:join") in tags

    registry.clear()


def test_hook_execution_adds_span_tags():
    """Test that hook execution adds span tags for observability."""
    registry = get_global_registry()
    registry.clear()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    # Enable and instrument
    _sca_detection_callback([MockPayload()])

    # Mock span
    mock_span = mock.Mock()

    # Call instrumented function with mocked span
    with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", return_value=mock_span):
        import os.path

        os.path.join("foo", "bar")

        # Should set span tags
        assert mock_span._set_tag_str.called
        calls = mock_span._set_tag_str.call_args_list

        # Check for instrumented tag
        instrumented_calls = [call for call in calls if SCA.TAG_INSTRUMENTED in call[0]]
        assert len(instrumented_calls) >= 1
        assert instrumented_calls[0][0] == (SCA.TAG_INSTRUMENTED, "true")

        # Check for detection hit tag
        hit_calls = [call for call in calls if SCA.TAG_DETECTION_HIT in call[0]]
        assert len(hit_calls) >= 1
        assert hit_calls[0][0] == (SCA.TAG_DETECTION_HIT, "true")

        # Check for target tag
        target_calls = [call for call in calls if SCA.TAG_TARGET in call[0]]
        assert len(target_calls) >= 1
        assert target_calls[0][0] == (SCA.TAG_TARGET, "os.path:join")

    registry.clear()


def test_hook_execution_without_span():
    """Test that hook execution handles missing span gracefully."""
    registry = get_global_registry()
    registry.clear()

    class MockPayload:
        def __init__(self):
            self.path = "sca/detection/config1"
            self.content = {"targets": ["os.path:join"]}
            self.metadata = {}

    # Enable and instrument
    _sca_detection_callback([MockPayload()])

    # Call instrumented function without span (should not raise)
    with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", return_value=None):
        with mock.patch("ddtrace.appsec.sca._instrumenter.telemetry_writer"):
            import os.path

            result = os.path.join("foo", "bar")
            assert result == os.path.join("foo", "bar")

    registry.clear()
