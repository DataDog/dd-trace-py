"""
Tests for the deadlock detector (tests/deadlock/_deadlock C extension + fixture).

Test strategy
-------------
Unit tests (in-process)
    Cover arm/disarm API correctness and the graceful no-op fallback.

Subprocess tests
    Run a minimal Python script that calls arm(), optionally disarm(), and then
    sleeps.  The parent process verifies the exit status and stderr content:
    - When the deadlock detector fires it kills the process via SIGABRT (returncode -6
      on Unix) and writes "DEADLOCK WATCHDOG FIRED" to stderr.
    - When disarmed in time the process exits cleanly (status 0, no dump).

Each subprocess test sets ``DD_TEST_DEADLOCK_TIMEOUT=0`` via the env kwarg so
the global autouse ``deadlock_watchdog`` fixture is disabled *inside* the
subprocess (which runs under pytest).  For the @pytest.mark.subprocess pattern
the function body is extracted and run as a standalone script – there is no
pytest fixture system active there – but the env var also silences the fixture
for any future code path changes that might run pytest in the subprocess.
"""

import signal
import sys

import pytest

from tests.deadlock import DEADLOCK_AVAILABLE
from tests.deadlock import arm
from tests.deadlock import disarm


# ---------------------------------------------------------------------------
# Override the global autouse deadlock_watchdog fixture for this module so
# that tests which intentionally trigger the deadlock detector are not themselves
# wrapped by a competing watchdog instance.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def deadlock_watchdog():
    """No-op override: deadlock tests manage arm/disarm themselves."""
    yield


# Convenience marker to skip tests that require the native extension
_native_only = pytest.mark.skipif(
    not DEADLOCK_AVAILABLE,
    reason="_deadlock native extension not built (run 'pip install -e .' first)",
)

# Skip subprocess tests on Windows (SIGABRT semantics differ)
_unix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGABRT subprocess exit-code semantics differ on Windows",
)


# ===========================================================================
# Unit tests (in-process, no subprocess)
# ===========================================================================


def test_no_op_fallback_available():
    """arm/disarm must always be importable, even without the native extension."""
    # Imports at module level already exercise this; just assert they exist.
    assert callable(arm)
    assert callable(disarm)


def test_disarm_without_prior_arm_is_safe():
    """disarm() without a preceding arm() must not raise."""
    disarm()  # must not raise


@_native_only
def test_arm_disarm_basic():
    """arm() / disarm() round-trip must succeed without error or spurious fire."""
    arm(timeout=300, test_name="test_arm_disarm_basic")
    disarm()


@_native_only
def test_arm_twice_replaces_watchdog():
    """Calling arm() twice must replace the old watchdog without crashing."""
    arm(timeout=300, test_name="first_arm")
    arm(timeout=300, test_name="second_arm")  # replaces first
    disarm()


@_native_only
def test_invalid_timeout_raises():
    """arm(timeout=0) and arm(timeout=-1) must raise ValueError."""
    with pytest.raises((ValueError, Exception)):
        arm(timeout=0)
    with pytest.raises((ValueError, Exception)):
        arm(timeout=-1)


@_native_only
def test_test_name_accepted():
    """arm() should accept an optional test_name without error."""
    arm(timeout=300, test_name="some::test::nodeid")
    disarm()


@_native_only
def test_enable_gdb_flag_accepted():
    """arm(enable_gdb=True) should be accepted without error (we just don't
    assert that gdb is installed).
    """
    arm(timeout=300, enable_gdb=True, test_name="test_enable_gdb_flag_accepted")
    disarm()


# ===========================================================================
# Subprocess tests – require native extension + Unix
# ===========================================================================


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=-signal.SIGABRT,
    err=lambda s: "DEADLOCK WATCHDOG FIRED" in s,
    env={"DD_TEST_DEADLOCK_TIMEOUT": "0"},
)
def test_watchdog_fires_on_timeout():
    """Deadlock detector must abort the process and dump stacks after the timeout.

    The subprocess arms the detector with a 1-second timeout then sleeps for
    10 seconds.  We expect:
      * exit status -SIGABRT (killed by SIGABRT)
      * stderr containing "DEADLOCK WATCHDOG FIRED"
    """
    import time

    from tests.deadlock import arm

    arm(timeout=1, test_name="test_watchdog_fires_on_timeout")
    time.sleep(10)  # deadlock detector fires after 1 s, kills us before we wake up


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=0,
    err=None,  # don't assert on stderr content; just check clean exit
    env={"DD_TEST_DEADLOCK_TIMEOUT": "0"},
)
def test_disarm_prevents_timeout():
    """Disarming before the deadline must prevent the detector from firing.

    The subprocess arms with a 1-second timeout, disarms after 0.1 s, then
    sleeps for another 2 seconds (past the original deadline).  It must exit
    cleanly with status 0.
    """
    import time

    from tests.deadlock import arm
    from tests.deadlock import disarm

    arm(timeout=1, test_name="test_disarm_prevents_timeout")
    time.sleep(0.1)  # well within the 1 s timeout
    disarm()
    time.sleep(2)  # past the original deadline; watchdog must NOT fire


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=-signal.SIGABRT,
    err=lambda s: "DEADLOCK WATCHDOG FIRED" in s and "Interpreter 0" in s,
    env={"DD_TEST_DEADLOCK_TIMEOUT": "0"},
)
def test_dump_includes_interpreter_and_thread_headers():
    """Stack dump must include the 'Interpreter N:' and 'Thread N (' headers."""
    import time

    from tests.deadlock import arm

    arm(timeout=1, test_name="test_dump_includes_interpreter_and_thread_headers")
    time.sleep(10)


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=-signal.SIGABRT,
    err=lambda s: "_sleeping_in_known_function" in s,
    env={"DD_TEST_DEADLOCK_TIMEOUT": "0"},
)
def test_dump_includes_python_frame_names():
    """Stack dump must include the name of the currently executing function.

    We define a helper called ``_sleeping_in_known_function`` and sleep inside
    it.  The dump must contain that name so we know the GIL-free frame walk
    produced real output.
    """
    import time

    from tests.deadlock import arm

    arm(timeout=1, test_name="test_dump_includes_python_frame_names")

    def _sleeping_in_known_function():
        time.sleep(10)

    _sleeping_in_known_function()


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=-signal.SIGABRT,
    err=lambda s: 'File "' in s and '", line ' in s and ", in " in s,
    env={"DD_TEST_DEADLOCK_TIMEOUT": "0"},
)
def test_dump_frame_format():
    """Each frame in the dump must match the expected format.

    Expected pattern per frame::

        File "<filename>", line <N>, in <funcname>
    """
    import time

    from tests.deadlock import arm

    arm(timeout=1, test_name="test_dump_frame_format")
    time.sleep(10)


# ===========================================================================
# Subprocess injection tests – verify conftest.py injects the watchdog
# ===========================================================================


@_native_only
@pytest.mark.subprocess()
def test_subprocess_marker_injects_watchdog_env():
    """run_function_from_file must set _DD_TEST_NODE_ID in the subprocess env.

    This verifies that the conftest.py injection mechanism ran and forwarded
    the test node ID to the subprocess without the test body calling arm()
    itself.
    """
    import os

    assert "_DD_TEST_NODE_ID" in os.environ, "_DD_TEST_NODE_ID not injected into subprocess env"
    assert "test_subprocess_marker_injects_watchdog_env" in os.environ["_DD_TEST_NODE_ID"]


@_native_only
@_unix_only
@pytest.mark.subprocess(
    status=-signal.SIGABRT,
    err=lambda s: "DEADLOCK WATCHDOG FIRED" in s,
    env={"_DD_DEADLOCK_SUBPROCESS_TIMEOUT": "1"},
)
def test_subprocess_marker_injects_watchdog_fires():
    """The injected watchdog must fire in a subprocess test that hangs.

    The test body does NOT call arm() itself; it relies entirely on the
    watchdog injected by run_function_from_file.  The timeout is set to 1 s
    via _DD_DEADLOCK_SUBPROCESS_TIMEOUT so the watchdog fires quickly.
    """
    import time

    time.sleep(60)  # injected watchdog fires after 1 s, kills us before we wake up
