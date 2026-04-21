"""Tests for SCA instrumentation: inject_hook fires through wrapt wrappers
and caller info is correctly propagated to the reached array.

Reproduces the system test bug where `reached` stays empty after calling
a vulnerable function through ddtrace's wrapt-patched wrappers.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import ddtrace.appsec.sca._instrumenter as _instrumenter_mod
from ddtrace.appsec.sca._instrumenter import Instrumenter
from ddtrace.appsec.sca._instrumenter import _first_instr_line
import ddtrace.appsec.sca._registry as _registry_mod
from ddtrace.appsec.sca._registry import InstrumentationRegistry
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker


@pytest.fixture(autouse=True)
def _restore_instrumenter_globals():
    """Save and restore module-level singletons and config to prevent cross-test contamination."""
    from ddtrace.internal.settings._config import config as tracer_config

    saved_registry = _instrumenter_mod._registry
    saved_instance = _instrumenter_mod._instrumenter_instance
    saved_global_registry = _registry_mod._global_registry
    saved_sca_enabled = tracer_config._sca_enabled
    yield
    _instrumenter_mod._registry = saved_registry
    _instrumenter_mod._instrumenter_instance = saved_instance
    _registry_mod._global_registry = saved_global_registry
    tracer_config._sca_enabled = saved_sca_enabled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target_function():
    """Create a simple function to instrument."""

    def vulnerable_func(x):
        return x + 1

    return vulnerable_func


def _make_wrapt_wrapped_function():
    """Create a function wrapped with wrapt, mimicking ddtrace monkey-patching."""
    from wrapt import FunctionWrapper

    original = _make_target_function()

    def wrapper(wrapped, instance, args, kwargs):
        return wrapped(*args, **kwargs)

    wrapped = FunctionWrapper(original, wrapper)
    return wrapped, original


# ---------------------------------------------------------------------------
# Test: inject_hook fires through direct call
# ---------------------------------------------------------------------------


class TestInjectHookFires:
    def test_hook_fires_on_direct_call(self):
        """inject_hook: hook fires when function is called directly."""
        called_with = []

        def my_hook(arg):
            called_with.append(arg)

        func = _make_target_function()
        inject_hook(func, my_hook, _first_instr_line(func.__code__), "test:target")

        result = func(10)
        assert result == 11
        assert called_with == ["test:target"]

    def test_hook_fires_through_wrapt_wrapper(self):
        """inject_hook: hook fires when function is called through a wrapt wrapper."""
        called_with = []

        def my_hook(arg):
            called_with.append(arg)

        wrapped_func, original = _make_wrapt_wrapped_function()
        inject_hook(original, my_hook, _first_instr_line(original.__code__), "test:wrapped_target")

        # Call through the wrapper — the wrapper calls original(*args, **kwargs)
        result = wrapped_func(10)
        assert result == 11
        assert called_with == ["test:wrapped_target"]

    def test_hook_fires_only_once_per_call(self):
        """inject_hook: hook fires exactly once per function invocation."""
        call_count = []

        def my_hook(arg):
            call_count.append(1)

        func = _make_target_function()
        inject_hook(func, my_hook, _first_instr_line(func.__code__), "test:once")

        func(1)
        func(2)
        func(3)
        assert len(call_count) == 3


# ---------------------------------------------------------------------------
# Test: full SCA detection hook flow with registry + telemetry
# ---------------------------------------------------------------------------


class TestSCADetectionHookIntegration:
    """Test the full sca_detection_hook → registry → telemetry flow."""

    def _setup_sca_flow(self):
        """Set up registry, instrumenter, tracker, and writer for SCA testing."""
        registry = InstrumentationRegistry()
        instrumenter = Instrumenter(registry)

        tracker = DependencyTracker()
        from ddtrace.internal.settings._config import config as tracer_config

        tracer_config._sca_enabled = True

        # Pre-populate a dependency with registered CVE
        entry = DependencyEntry(name="fakepkg", version="1.0.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["fakepkg"] = entry

        # Register a CVE (reached=[])
        entry.add_metadata("CVE-2024-TEST")

        # Mark initial metadata as sent (simulates first heartbeat)
        entry.mark_all_metadata_sent()

        return registry, instrumenter, tracker, entry

    def test_hook_populates_reached_array(self):
        """Full flow: instrument → call → reached array has caller info."""
        registry, instrumenter, tracker, entry = self._setup_sca_flow()

        # Create and register a target function
        def user_calls_this():
            return target_func(42)

        def target_func(x):
            return x * 2

        registry.add_target(
            "test_module:target_func",
            package_name="fakepkg",
            cve_ids=["CVE-2024-TEST"],
        )
        instrumenter.instrument("test_module:target_func", target_func)

        # Mock the telemetry writer to capture attach_dependency_metadata calls
        mock_writer = MagicMock()
        attach_calls = []

        def capture_attach(package_name, cve_id, path, symbol, line):
            attach_calls.append({"pkg": package_name, "cve": cve_id, "path": path, "symbol": symbol, "line": line})

        mock_writer.attach_dependency_metadata = capture_attach

        with patch("ddtrace.appsec.sca._instrumenter.telemetry_writer", mock_writer):
            # Call the function — the hook should fire
            result = target_func(42)

        assert result == 84
        assert len(attach_calls) == 1
        assert attach_calls[0]["pkg"] == "fakepkg"
        assert attach_calls[0]["cve"] == "CVE-2024-TEST"
        # The hook fired and called attach_dependency_metadata

    def test_hook_fires_through_wrapt_and_reports(self):
        """Full flow through wrapt: instrument unwrapped → call via wrapper → hook fires."""
        from wrapt import FunctionWrapper

        registry, instrumenter, tracker, entry = self._setup_sca_flow()

        def target_func(x):
            return x * 2

        # Wrap with wrapt (like ddtrace monkey-patching)
        def wrapper(wrapped, instance, args, kwargs):
            return wrapped(*args, **kwargs)

        wrapt_wrapper = FunctionWrapper(target_func, wrapper)

        # Register and instrument the ORIGINAL function (unwrapped)
        registry.add_target(
            "test_module:target_func",
            package_name="fakepkg",
            cve_ids=["CVE-2024-TEST"],
        )
        instrumenter.instrument("test_module:target_func", target_func)

        # Track calls
        attach_calls = []
        mock_writer = MagicMock()
        mock_writer.attach_dependency_metadata = lambda package_name, cve_id, path, symbol, line: attach_calls.append(
            {"pkg": package_name, "cve": cve_id, "path": path, "symbol": symbol, "line": line}
        )

        with patch("ddtrace.appsec.sca._instrumenter.telemetry_writer", mock_writer):
            # Call through the WRAPPER — hook should still fire
            result = wrapt_wrapper(42)

        assert result == 84
        assert len(attach_calls) == 1, (
            f"Expected 1 attach call, got {len(attach_calls)}: hook did not fire through wrapt"
        )
        assert attach_calls[0]["pkg"] == "fakepkg"
        assert attach_calls[0]["cve"] == "CVE-2024-TEST"


# ---------------------------------------------------------------------------
# Test: caller info propagation to reached array (the actual bug)
# ---------------------------------------------------------------------------


class TestCallerInfoToReachedArray:
    """Tests that caller info makes it from the hook all the way to the reached array.

    This reproduces the system test bug: the hook fires, but _get_caller_info()
    returns empty values, and the `if path` guard in add_metadata() silently
    drops the finding, leaving reached=[].
    """

    def test_reached_populated_when_caller_info_available(self):
        """When _get_caller_info returns valid data, reached array is populated."""
        registry = InstrumentationRegistry()
        instrumenter = Instrumenter(registry)

        tracker = DependencyTracker()
        from ddtrace.internal.settings._config import config as tracer_config

        tracer_config._sca_enabled = True

        entry = DependencyEntry(name="fakepkg", version="1.0.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["fakepkg"] = entry
        entry.add_metadata("CVE-2024-TEST")  # Register CVE with reached=[]
        entry.mark_all_metadata_sent()

        def target_func(x):
            return x * 2

        registry.add_target(
            "test_module:target_func",
            package_name="fakepkg",
            cve_ids=["CVE-2024-TEST"],
        )
        instrumenter.instrument("test_module:target_func", target_func)

        # Mock _get_caller_info to return known values
        mock_writer = MagicMock()
        mock_writer.attach_dependency_metadata = lambda package_name, cve_id, path, symbol, line: (
            tracker.attach_metadata(package_name, cve_id, path, symbol, line)
        )

        with (
            patch("ddtrace.appsec.sca._instrumenter.telemetry_writer", mock_writer),
            patch(
                "ddtrace.appsec.sca._instrumenter._get_caller_info",
                return_value=("app/views.py", 42, "MyView.handle"),
            ),
        ):
            target_func(10)

        # Verify the reached array was populated
        meta = entry.metadata[0]
        reached = meta.value["reached"]
        assert len(reached) == 1, f"Expected 1 reached entry, got {len(reached)}"
        assert reached[0]["path"] == "app/views.py"
        assert reached[0]["symbol"] == "MyView.handle"
        assert reached[0]["line"] == 42

    def test_hook_uses_fallback_when_caller_info_unavailable(self):
        """Regression: when _get_caller_info returns empty path, the hook falls
        back to the target's qualified name so reached is never left empty.

        Before the fix, add_metadata's `if path` guard silently dropped
        the finding and reached stayed [].
        """
        registry = InstrumentationRegistry()
        instrumenter = Instrumenter(registry)

        tracker = DependencyTracker()
        from ddtrace.internal.settings._config import config as tracer_config

        tracer_config._sca_enabled = True

        entry = DependencyEntry(name="fakepkg", version="1.0.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["fakepkg"] = entry
        entry.add_metadata("CVE-2024-TEST")
        entry.mark_all_metadata_sent()

        def target_func(x):
            return x * 2

        registry.add_target(
            "test_module:target_func",
            package_name="fakepkg",
            cve_ids=["CVE-2024-TEST"],
        )
        instrumenter.instrument("test_module:target_func", target_func)

        mock_writer = MagicMock()
        mock_writer.attach_dependency_metadata = lambda package_name, cve_id, path, symbol, line: (
            tracker.attach_metadata(package_name, cve_id, path, symbol, line)
        )

        # Simulate _get_caller_info returning empty (native frame walker failure)
        with (
            patch("ddtrace.appsec.sca._instrumenter.telemetry_writer", mock_writer),
            patch(
                "ddtrace.appsec.sca._instrumenter._get_caller_info",
                return_value=("", 0, ""),
            ),
        ):
            target_func(10)

        meta = entry.metadata[0]
        reached = meta.value["reached"]
        # After fix: fallback uses the target's module path and symbol name
        assert len(reached) == 1, f"Expected 1 reached entry with fallback info, got {len(reached)}"
        assert reached[0]["path"] == "test_module"
        assert reached[0]["symbol"] == "target_func"
        assert reached[0]["line"] == 0


# ---------------------------------------------------------------------------
# Test: fork callback ordering (the system test bug)
# ---------------------------------------------------------------------------


class TestForkCallbackOrdering:
    """Reproduces the system test bug where os.register_at_fork callbacks
    wipe _registry after restart() has already set it up.

    The fix: restart() explicitly resets state before re-init, and
    os.register_at_fork is not used.
    """

    def test_restart_resets_then_reinits_registry(self):
        """restart() must reset state then re-init. After calling
        apply_instrumentation_updates, _registry must be set.
        """
        import ddtrace.appsec.sca._instrumenter as instrumenter_mod
        from ddtrace.appsec.sca._instrumenter import _reset_after_fork
        from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates

        # Start clean
        _reset_after_fork()
        assert instrumenter_mod._registry is None

        # Simulate what restart() → _load_and_instrument() → apply_instrumentation_updates does
        apply_instrumentation_updates(targets=[])

        # After apply_instrumentation_updates, _registry must be set via set_registry()
        assert instrumenter_mod._registry is not None, "_registry is None after apply_instrumentation_updates"

    def test_register_at_fork_not_used(self):
        """os.register_at_fork must NOT be registered in _instrumenter.py or _registry.py.

        If it were, the CPython fork callback would fire AFTER restart() and
        wipe _registry=None permanently.
        """
        import ddtrace.appsec.sca._instrumenter as instrumenter_mod
        from ddtrace.appsec.sca._instrumenter import _reset_after_fork
        from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates

        # Set up _registry
        _reset_after_fork()
        apply_instrumentation_updates(targets=[])
        assert instrumenter_mod._registry is not None

        # Simulate the OLD bug: os.register_at_fork callback fires after restart
        _reset_after_fork()
        assert instrumenter_mod._registry is None, "After explicit reset, _registry is None"

        # This demonstrates the bug: if register_at_fork ran after restart(),
        # it would wipe the registry that restart() just set up.
        # The fix: restart() calls _reset_after_fork() itself, and
        # os.register_at_fork is not used.

    def test_restart_calls_reset_before_reinit(self):
        """restart() calls _reset_after_fork BEFORE _load_and_instrument,
        ensuring a clean state for re-initialization.
        """
        from ddtrace.internal.sca.product import restart

        reset_calls = []

        def track_reset():
            reset_calls.append("instrumenter_reset")

        def track_registry_reset():
            reset_calls.append("registry_reset")

        with (
            patch("ddtrace.internal.sca.product.tracer_config") as mock_config,
            patch(
                "ddtrace.appsec.sca._instrumenter._reset_after_fork",
                side_effect=track_reset,
            ),
            patch(
                "ddtrace.appsec.sca._registry._reset_global_registry_after_fork",
                side_effect=track_registry_reset,
            ),
            patch("ddtrace.internal.sca.product.start"),
            patch("ddtrace.internal.sca.product._load_and_instrument") as mock_load,
            patch("ddtrace.internal.sca.product.stop"),
        ):
            mock_config._sca_enabled = True  # Must be True or restart() returns early
            restart()

        # Both resets should have been called
        assert "instrumenter_reset" in reset_calls, "restart() must call _reset_after_fork"
        assert "registry_reset" in reset_calls, "restart() must call _reset_global_registry_after_fork"
        # restart() must pass after_fork=True to skip re-injecting hooks
        # on functions whose bytecode already carries the hook from the parent.
        mock_load.assert_called_once_with(after_fork=True)
