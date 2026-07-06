# -*- encoding: utf-8 -*-
import multiprocessing
import os
import sys

import pytest

from tests.profiling.collector import lock_utils
from tests.profiling.collector import pprof_utils
from tests.profiling.utils import get_profile_from_agent
from tests.profiling.utils import with_profiling_test_agent
from tests.utils import call_program


def test_call_script() -> None:
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program.py"), env=env
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)

    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    hello, _ = list(s.strip() for s in stdout.strip().split("\n"))
    assert hello == "hello world", stdout.strip()


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_call_script_gevent():
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_gevent.py"), env=env
    )
    assert exitcode == 0, (stdout, stderr)


def test_call_script_pprof_output() -> None:
    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    with with_profiling_test_agent() as agent_client:
        env = os.environ.copy()
        env["DD_PROFILING_CAPTURE_PCT"] = "100"
        env["DD_PROFILING_ENABLED"] = "1"
        stdout, stderr, exitcode, _ = call_program(
            "ddtrace-run",
            sys.executable,
            os.path.join(os.path.dirname(__file__), "simple_program.py"),
            env=env,
        )
        if sys.platform == "win32":
            assert exitcode == 0, (stdout, stderr)
        else:
            assert exitcode == 42, (stdout, stderr)

        profile = get_profile_from_agent(agent_client)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork() -> None:
    from tests.profiling.utils import get_all_profiles_from_agent

    parent_expected_acquire_events = [
        pprof_utils.LockAcquireEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
            lock_name="lock",
        ),
    ]
    parent_expected_release_events = [
        pprof_utils.LockReleaseEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
            lock_name="lock",
        ),
    ]
    child_expected_acquire_events = [
        # After fork(), we clear the samples in child, so we only have one
        # lock acquire event
        pprof_utils.LockAcquireEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=24, acquire=25, release=26),
            lock_name="lock",
        ),
    ]
    child_expected_release_events = [
        pprof_utils.LockReleaseEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=11, acquire=12, release=21),
            lock_name="lock",
        ),
        pprof_utils.LockReleaseEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=24, acquire=25, release=26),
            lock_name="lock",
        ),
    ]

    with with_profiling_test_agent() as agent_client:
        env = os.environ.copy()
        env["DD_PROFILING_CAPTURE_PCT"] = "100"
        stdout, stderr, exitcode, pid = call_program(
            sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_fork.py"), env=env
        )
        assert exitcode == 0, stderr
        profiles = get_all_profiles_from_agent(agent_client)
    assert len(profiles) >= 2, f"Expected at least 2 profiling uploads (parent + child), got {len(profiles)}"

    # Identify parent and child profiles by the events they contain:
    # The parent profile has lock events from lines 11/12/28 (original lock),
    # while the child profile only has events from lines 24/25/26 (new lock after fork).
    parent_profile = None
    child_profile = None
    for profile in profiles:
        try:
            pprof_utils.assert_lock_events(
                profile,
                expected_acquire_events=parent_expected_acquire_events,
                expected_release_events=parent_expected_release_events,
            )
            parent_profile = profile
        except AssertionError:
            pass

        try:
            pprof_utils.assert_lock_events(
                profile,
                expected_acquire_events=child_expected_acquire_events,
                expected_release_events=child_expected_release_events,
            )
            child_profile = profile
        except AssertionError:
            pass

    assert parent_profile is not None, "No profile found with parent lock events (lines 11/12/28)"
    assert child_profile is not None, "No profile found with child lock events (lines 24/25/26)"

    # Verify parent profile has the expected events
    pprof_utils.assert_lock_events(
        parent_profile,
        expected_acquire_events=parent_expected_acquire_events,
        expected_release_events=parent_expected_release_events,
        print_samples_on_failure=True,
    )

    # We expect the child profile to not have lock events from the parent process
    # Note that assert_lock_events function only checks that the given events
    # exists, and doesn't assert that other events don't exist.
    with pytest.raises(AssertionError):
        pprof_utils.assert_lock_events(
            child_profile,
            expected_acquire_events=parent_expected_acquire_events,
            expected_release_events=parent_expected_release_events,
        )
    pprof_utils.assert_lock_events(
        child_profile,
        expected_acquire_events=child_expected_acquire_events,
        expected_release_events=child_expected_release_events,
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent() -> None:
    env = os.environ.copy()
    _, _, exitcode, _ = call_program("python", os.path.join(os.path.dirname(__file__), "gevent_fork.py"), env=env)
    assert exitcode == 0


methods = multiprocessing.get_all_start_methods()


@pytest.mark.parametrize(
    "method",
    set(methods) - {"forkserver", "fork"},
)
def test_multiprocessing(method: str) -> None:
    from tests.profiling.utils import get_all_profiles_from_agent

    with with_profiling_test_agent() as agent_client:
        env = os.environ.copy()
        env["DD_PROFILING_ENABLED"] = "1"
        env["DD_PROFILING_CAPTURE_PCT"] = "100"
        stdout, stderr, exitcode, _ = call_program(
            "ddtrace-run",
            sys.executable,
            os.path.join(os.path.dirname(__file__), "_test_multiprocessing.py"),
            method,
            env=env,
        )
        assert exitcode == 0, (stdout, stderr)
        profiles = get_all_profiles_from_agent(agent_client)
    # Expect at least 2 profiles: one for the parent process and one for the child
    assert len(profiles) >= 2, f"Expected at least 2 profiling uploads (parent + child), got {len(profiles)}"
    # At least one profile must have cpu-time samples (early/empty flush profiles are allowed)
    assert any(len(pprof_utils.get_samples_with_value_type(p, "cpu-time")) > 0 for p in profiles), (
        "No profiling uploads had cpu-time samples"
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1"),
    err=lambda _: "RuntimeError: the memalloc module is already started" not in _,
)
def test_memalloc_no_init_error_on_fork() -> None:
    import os

    pid = os.fork()
    if not pid:
        exit(0)
    os.waitpid(pid, 0)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="1",
    ),
    out="OK\n",
    err=None,
)
def test_profiler_start_up_with_module_clean_up_in_protobuf_app() -> None:
    # This can cause segfaults if we do module clean up with later versions of
    # protobuf. This is a regression test.
    from google.protobuf import empty_pb2  # noqa:F401

    print("OK")
