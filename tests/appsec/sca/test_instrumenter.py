"""Tests for SCA instrumentation: inject_hook fires through wrapt wrappers
and caller info is correctly propagated to the reached array.

Reproduces the system test bug where `reached` stays empty after calling
a vulnerable function through ddtrace's wrapt-patched wrappers.
"""

import json
from types import FunctionType
from unittest.mock import MagicMock
from unittest.mock import patch

from ddtrace.appsec.sca._instrumenter import Instrumenter
from ddtrace.appsec.sca._instrumenter import sca_detection_hook
from ddtrace.appsec.sca._registry import InstrumentationRegistry
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency import ReachabilityMetadata
from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker


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
        inject_hook(func, my_hook, func.__code__.co_firstlineno, "test:target")

        result = func(10)
        assert result == 11
        assert called_with == ["test:target"]

    def test_hook_fires_through_wrapt_wrapper(self):
        """inject_hook: hook fires when function is called through a wrapt wrapper."""
        called_with = []

        def my_hook(arg):
            called_with.append(arg)

        wrapped_func, original = _make_wrapt_wrapped_function()
        inject_hook(original, my_hook, original.__code__.co_firstlineno, "test:wrapped_target")

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
        inject_hook(func, my_hook, func.__code__.co_firstlineno, "test:once")

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
        tracker._sca_metadata_enabled = True

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

        def capture_attach(pkg, cve, path, method, line):
            attach_calls.append({"pkg": pkg, "cve": cve, "path": path, "method": method, "line": line})

        mock_writer.attach_dependency_metadata = capture_attach

        with patch("ddtrace.appsec.sca._instrumenter._telemetry_writer", mock_writer):
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
        mock_writer.attach_dependency_metadata = lambda pkg, cve, path, method, line: attach_calls.append(
            {"pkg": pkg, "cve": cve, "path": path, "method": method, "line": line}
        )

        with patch("ddtrace.appsec.sca._instrumenter._telemetry_writer", mock_writer):
            # Call through the WRAPPER — hook should still fire
            result = wrapt_wrapper(42)

        assert result == 84
        assert len(attach_calls) == 1, f"Expected 1 attach call, got {len(attach_calls)}: hook did not fire through wrapt"
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
        tracker._sca_metadata_enabled = True

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
        mock_writer.attach_dependency_metadata = lambda pkg, cve, path, method, line: (
            tracker.attach_metadata(pkg, cve, path, method, line)
        )

        with (
            patch("ddtrace.appsec.sca._instrumenter._telemetry_writer", mock_writer),
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
        assert reached[0]["method"] == "MyView.handle"
        assert reached[0]["line"] == 42

    def test_reached_not_empty_when_caller_info_empty_after_fix(self):
        """Regression test: when _get_caller_info returns empty path,
        the hook falls back to the target's qualified name.

        Before the fix, add_metadata's `if path` guard silently dropped
        the finding and reached stayed [].
        """
        registry = InstrumentationRegistry()
        instrumenter = Instrumenter(registry)

        tracker = DependencyTracker()
        tracker._sca_metadata_enabled = True

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

        # Mock _get_caller_info to return EMPTY values (simulates native frame walker failure)
        mock_writer = MagicMock()
        mock_writer.attach_dependency_metadata = lambda pkg, cve, path, method, line: (
            tracker.attach_metadata(pkg, cve, path, method, line)
        )

        with (
            patch("ddtrace.appsec.sca._instrumenter._telemetry_writer", mock_writer),
            patch(
                "ddtrace.appsec.sca._instrumenter._get_caller_info",
                return_value=("", 0, ""),
            ),
        ):
            target_func(10)

        # After fix: reached has a fallback entry with the target's qualified name
        meta = entry.metadata[0]
        reached = meta.value["reached"]
        assert len(reached) == 1, f"Reached should have fallback entry, got {len(reached)}"
        assert reached[0]["path"] == "test_module"
        assert reached[0]["method"] == "target_func"

    def test_hook_uses_fallback_when_caller_info_unavailable(self):
        """After fix: when _get_caller_info returns empty, the hook falls back
        to the target's own qualified name so reached is never left empty.
        """
        registry = InstrumentationRegistry()
        instrumenter = Instrumenter(registry)

        tracker = DependencyTracker()
        tracker._sca_metadata_enabled = True

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
        mock_writer.attach_dependency_metadata = lambda pkg, cve, path, method, line: (
            tracker.attach_metadata(pkg, cve, path, method, line)
        )

        # Simulate _get_caller_info returning empty (native frame walker failure)
        with (
            patch("ddtrace.appsec.sca._instrumenter._telemetry_writer", mock_writer),
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
        assert reached[0]["method"] == "target_func"
        assert reached[0]["line"] == 0
