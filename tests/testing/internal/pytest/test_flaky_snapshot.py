from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
from ddtrace.testing.internal.pytest.flaky_snapshot import is_known_flaky_test
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestTag
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


def _ref(name: str = "test_it") -> TestRef:
    mod = ModuleRef("pkg.mod")
    return TestRef(SuiteRef(mod, "test_things.py"), name)


# =============================================================================
# Unit tests for is_known_flaky_test
# =============================================================================


def test_is_known_flaky_false_when_test_management_disabled():
    mgr = MagicMock()
    mgr.settings.test_management.enabled = False
    mgr.test_properties = {_ref(): TestProperties(quarantined=True)}
    assert is_known_flaky_test(mgr, _ref()) is False


def test_is_known_flaky_false_when_not_in_properties():
    mgr = MagicMock()
    mgr.settings.test_management.enabled = True
    mgr.test_properties = {}
    assert is_known_flaky_test(mgr, _ref()) is False


def test_is_known_flaky_false_when_not_quarantined():
    mgr = MagicMock()
    mgr.settings.test_management.enabled = True
    r = _ref()
    mgr.test_properties = {r: TestProperties(quarantined=False)}
    assert is_known_flaky_test(mgr, r) is False


def test_is_known_flaky_true_when_quarantined():
    mgr = MagicMock()
    mgr.settings.test_management.enabled = True
    r = _ref()
    mgr.test_properties = {r: TestProperties(quarantined=True)}
    assert is_known_flaky_test(mgr, r) is True


# =============================================================================
# Integration test: probe lifecycle and snapshot correlation tags
# =============================================================================


class TestKnownFlakySnapshotIntegration:
    """
    End-to-end test for the known-flaky snapshot feature using a real pytester session.

    The test file is the canonical "state-leak flakiness" example:
    - test_a_pollutes_state appends to a module-level list (a_ prefix ensures it
      runs first under alphabetical collection order).
    - test_b_flaky_depends_on_clean_state passes in isolation but fails when run
      after test_a_pollutes_state because the module-level list is non-empty.

    test_b_flaky_depends_on_clean_state is marked as known-flaky in the mocked
    API response.  The test verifies:
    1. A DI probe is registered before the test body runs.
    2. When the probe fires, the snapshot UUID is captured via the collector.push
       wrapper and all four correlation tags are set on the test span:
         error.debug_info_captured
         _dd.debug.error.0.snapshot_id
         _dd.debug.error.0.file
         _dd.debug.error.0.line
    3. Non-flaky test spans do not receive any of these tags.
    4. DI is disabled again at session end because we started it.

    The DI Debugger is mocked to avoid starting real instrumentation.  To
    simulate the probe actually firing, the mock's _on_configuration side effect
    calls collector.push with a fake signal after the collector wrapper is already
    in place (which happens before _on_configuration is called inside
    known_flaky_probe_context).
    """

    SIMULATED_SNAPSHOT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_probe_registered_snapshot_tags_set(self, pytester: Pytester) -> None:
        # AIDEV-NOTE: The "a_" / "b_" prefix forces alphabetical collection order
        # so test_a_pollutes_state always runs before test_b_flaky_depends_on_clean_state.
        pytester.makepyfile(
            test_state_leak="""
            # Canonical state-leak flakiness example.
            # test_a_pollutes_state appends to a module-level list without cleaning up.
            # test_b_flaky_depends_on_clean_state fails when run after it but passes alone.
            _registry = []

            def test_a_pollutes_state():
                _registry.append("leaked-item")

            def test_b_flaky_depends_on_clean_state():
                assert _registry == [], f"State leaked from a previous test: {_registry}"
            """
        )

        flaky_ref = TestRef(
            SuiteRef(ModuleRef(""), "test_state_leak.py"),
            "test_b_flaky_depends_on_clean_state",
        )
        test_properties = {flaky_ref: TestProperties(quarantined=True)}

        # Mock DI: track which probes are registered.
        registered_probes: list = []
        mock_dbg = Mock()

        # Simulate the probe firing: when _on_configuration receives NEW_PROBES
        # the collector.push wrapper is already in place (known_flaky_probe_context
        # wraps before registering), so pushing here triggers UUID capture.
        def simulate_probe_fire(event: ProbePollerEvent, probes: list) -> None:
            if event == ProbePollerEvent.NEW_PROBES and probes:
                probe = probes[0]
                registered_probes.append(probe)
                collector = MockUploader.get_collector.return_value
                fake_signal = Mock()
                fake_signal.probe.probe_id = probe.probe_id
                fake_signal.uuid = self.SIMULATED_SNAPSHOT_UUID
                collector.push(fake_signal)

        mock_dbg._on_configuration.side_effect = simulate_probe_fire

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    test_management_enabled=True,
                    test_properties=test_properties,
                ),
            ),
            setup_standard_mocks(),
            patch("ddtrace.testing.internal.pytest.flaky_snapshot.Debugger") as MockDebugger,
            patch("ddtrace.testing.internal.pytest.flaky_snapshot.SignalUploader") as MockUploader,
            patch("ddtrace.testing.internal.pytest.flaky_snapshot.FLAKY_SNAPSHOT_ENABLED", True),
            patch("ddtrace.testing.internal.pytest.plugin.FLAKY_SNAPSHOT_ENABLED", True),
        ):
            MockDebugger._instance = None
            MockDebugger.enable.side_effect = lambda: setattr(MockDebugger, "_instance", mock_dbg)
            # Collector must be non-None so the "unavailable" guard passes.
            mock_collector = Mock()
            MockUploader.get_collector.return_value = mock_collector

            with EventCapture.capture() as events:
                result = pytester.inline_run("--ddtrace", "-v", "-p", "no:randomly")

        # test_a_pollutes_state passes; test_b_flaky_depends_on_clean_state fails but is quarantined.
        assert_stats(result, passed=1, quarantined=1)

        # --- Probe was registered exactly once ---
        assert len(registered_probes) == 1

        # --- All four correlation tags are on the flaky test span ---
        [flaky_event] = list(events.events_by_test_name("test_b_flaky_depends_on_clean_state"))
        meta = flaky_event["content"]["meta"]

        assert meta.get(TestTag.KNOWN_FLAKY_DEBUG_INFO_CAPTURED) == "true"
        assert meta.get(TestTag.KNOWN_FLAKY_SNAPSHOT_ID) == self.SIMULATED_SNAPSHOT_UUID
        assert meta.get(TestTag.KNOWN_FLAKY_SNAPSHOT_FILE) != ""
        assert meta.get(TestTag.KNOWN_FLAKY_SNAPSHOT_LINE) != ""

        # The quarantined test's xfail wrapping converts the failure to an xfail (skip in span status).
        assert meta.get("test.status") == "skip"

        # --- Non-flaky test span has none of these tags ---
        [normal_event] = list(events.events_by_test_name("test_a_pollutes_state"))
        normal_meta = normal_event["content"]["meta"]
        assert TestTag.KNOWN_FLAKY_DEBUG_INFO_CAPTURED not in normal_meta
        assert TestTag.KNOWN_FLAKY_SNAPSHOT_ID not in normal_meta

        # --- DI was enabled at start and disabled at session end ---
        MockDebugger.enable.assert_called_once()
        MockDebugger.disable.assert_called_once()
