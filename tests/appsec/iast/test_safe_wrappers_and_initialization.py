"""
Unit tests for safe wrapper functions and native state initialization.

These tests verify that:
1. Safe wrapper functions handle uninitialized state gracefully
2. initialize_native_state() properly initializes the native module
3. reset_native_state() properly cleans up state
4. No crashes occur when calling IAST functions before initialization
"""

import subprocess
import sys

import pytest


@pytest.mark.skip_iast_check_logs
class TestUninitializedStateHandling:
    """Test that IAST functions handle uninitialized state gracefully."""

    def test_context_functions_before_init_return_safe_defaults(self):
        """Test that context functions return safe defaults before initialization.

        This test runs in a subprocess to ensure complete isolation from other
        tests that may have already initialized the native state.
        """
        # Run in subprocess for complete isolation
        code = """
import sys
# Import without calling initialize_native_state()
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context

# These should all return safe defaults without crashing
size = debug_context_array_size()
assert size == 0, f"Should return 0 before initialization, got {size}"

free_slots = debug_context_array_free_slots_number()
assert free_slots == 0, f"Should return 0 before initialization, got {free_slots}"

ctx = start_request_context()
assert ctx is None, f"Should return None before initialization, got {ctx}"

# This should not crash
finish_request_context(0)

num = debug_num_tainted_objects(0)
assert num == 0, f"Should return 0 before initialization, got {num}"

sys.exit(0)
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        if result.returncode != 0:
            pytest.fail(f"Subprocess test failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")

    def test_taint_functions_before_init_do_not_crash(self):
        """Test that taint functions don't crash before initialization."""
        from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

        test_str = "test string"
        # This should not crash, should return False
        result = is_pyobject_tainted(test_str)
        assert result is False, "Should return False before initialization"


class TestNativeStateInitialization:
    """Test the initialize_native_state() function."""

    def test_initialize_native_state_creates_globals(self):
        """Test that initialize_native_state() properly initializes globals."""
        from ddtrace.appsec._iast._taint_tracking import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking import reset_native_state

        # Reset to clean state
        reset_native_state()

        # Initialize
        initialize_native_state()

        # Verify context functions now work
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
        from ddtrace.appsec._iast._taint_tracking._context import start_request_context

        size = debug_context_array_size()
        assert size > 0, "Should return actual size after initialization"

        free_slots = debug_context_array_free_slots_number()
        assert free_slots > 0, "Should return actual free slots after initialization"

        ctx = start_request_context()
        assert ctx is not None, "Should return valid context ID after initialization"

    def test_taint_operations_work_after_initialization(self):
        """Test that taint operations work correctly after initialization."""
        from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
        from ddtrace.appsec._iast._overhead_control_engine import oce
        from ddtrace.appsec._iast._taint_tracking import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking import reset_native_state
        from ddtrace.appsec._iast._taint_tracking._context import start_request_context
        from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
        from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
        from ddtrace.internal.settings.asm import config as asm_config
        from tests.utils import override_env

        # Reset and initialize with proper configuration
        reset_native_state()

        # Configure IAST and overhead control engine
        with override_env({"DD_IAST_REQUEST_SAMPLING": "100", "DD_IAST_MAX_CONCURRENT_REQUEST": "100"}):
            asm_config._iast_enabled = True
            oce.reconfigure()
            initialize_native_state()

            # Create a request context and set it in the ContextVar
            ctx_id = start_request_context()
            assert ctx_id is not None, "Should return valid context ID after initialization"
            IAST_CONTEXT.set(ctx_id)

            # Test tainting
            tainted_str = taint_pyobject("sensitive data", source_name="test_source", source_value="test_value")
            assert tainted_str is not None

            result = is_pyobject_tainted(tainted_str)
            assert result is True, "String should be tainted after tainting operation"


class TestResetNativeState:
    """Test the reset_native_state() function."""

    def test_reset_native_state_clears_globals(self):
        """Test that reset_native_state() properly cleans up and re-initializes."""
        from ddtrace.appsec._iast._taint_tracking import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking import reset_native_state
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size

        # Initialize
        initialize_native_state()

        size_before = debug_context_array_size()
        assert size_before > 0, "Should have size after initialization"

        # Reset (which re-initializes)
        reset_native_state()

        # Verify state is fresh (re-initialized, not nullptr)
        size_after = debug_context_array_size()
        assert size_after > 0, "Should have size after reset (which re-initializes)"

        free_slots_after = debug_context_array_free_slots_number()
        assert free_slots_after > 0, "Should have free slots after reset (which re-initializes)"


class TestForkSafety:
    """Test that fork safety mechanisms work correctly."""

    def test_state_is_reset_in_child_process_after_fork(self):
        """Test that child process has clean state after fork."""
        import os

        from ddtrace.appsec._iast._taint_tracking import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
        from ddtrace.appsec._iast._taint_tracking._context import start_request_context
        from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
        from ddtrace.internal.settings.asm import config as asm_config

        # Enable IAST
        asm_config._iast_enabled = True

        # Initialize and create tainted objects in parent
        initialize_native_state()
        ctx_id = start_request_context()
        assert ctx_id is not None

        # Create some tainted objects in parent
        for i in range(10):
            test_str = f"tainted_string_{i}"
            try:
                taint_pyobject(test_str, source_name=f"source_{i}", source_value=f"value_{i}")
            except Exception:
                pass  # Some tainting might fail, that's ok for this test

        # Fork
        pid = os.fork()

        if pid == 0:
            # Child process
            try:
                # Verify state was reset in child
                child_size = debug_context_array_size()
                child_free = debug_context_array_free_slots_number()

                # With pthread_atfork fix, child should have all slots free
                if child_free == child_size and child_size > 0:
                    os._exit(0)  # Success
                else:
                    os._exit(1)  # Failure - state not reset
            except Exception:
                os._exit(2)  # Exception
        else:
            # Parent process
            _, status = os.waitpid(pid, 0)

            if os.WIFSIGNALED(status):
                signal_num = os.WTERMSIG(status)
                pytest.fail(f"Child crashed with signal {signal_num}")
            elif os.WIFEXITED(status):
                exit_code = os.WEXITSTATUS(status)
                if exit_code == 0:
                    # Test passed
                    pass
                elif exit_code == 1:
                    pytest.fail("State was not reset in child process")
                else:
                    pytest.fail(f"Child process error: {exit_code}")


class TestSafeWrappersPreventCrashes:
    """Test that safe wrapper functions prevent crashes in error scenarios."""

    def test_multiple_initializations_do_not_crash(self):
        """Test that calling initialize multiple times doesn't crash."""
        from ddtrace.appsec._iast._taint_tracking._native import initialize_native_state

        # This should not crash
        initialize_native_state()
        initialize_native_state()
        initialize_native_state()

    def test_multiple_resets_do_not_crash(self):
        """Test that calling reset multiple times doesn't crash."""
        from ddtrace.appsec._iast._taint_tracking._native import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking._native import reset_native_state

        initialize_native_state()
        reset_native_state()
        reset_native_state()  # Should not crash

    def test_operations_after_reset_return_safe_values(self):
        """Test that operations after reset work correctly (reset re-initializes)."""
        from ddtrace.appsec._iast._taint_tracking import initialize_native_state
        from ddtrace.appsec._iast._taint_tracking import reset_native_state
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
        from ddtrace.appsec._iast._taint_tracking._context import start_request_context
        from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

        # Initialize, then reset (which re-initializes)
        initialize_native_state()
        reset_native_state()

        # Operations should work (because reset re-initializes)
        size = debug_context_array_size()
        assert size > 0, "Should have size after reset (which re-initializes)"

        ctx = start_request_context()
        assert ctx is not None, "Should be able to create context after reset"

        result = is_pyobject_tainted("test")
        assert result is False, "Untainted string should return False"
