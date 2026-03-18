import _thread
import os
from pathlib import Path
import sys
import time
from unittest import mock

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from tests.profiling.collector import pprof_utils


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
def test_start_native_monitoring_raises_runtime_error() -> None:
    """start_native_monitoring raises RuntimeError when PROFILER_ID is already claimed."""
    from ddtrace.internal.datadog.profiling.stack._stack import start_native_monitoring

    sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "conflict")
    try:
        with pytest.raises(RuntimeError, match="sys.monitoring PROFILER_ID is already claimed"):
            start_native_monitoring()
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
def test_start_catches_runtime_error(caplog: pytest.LogCaptureFixture) -> None:
    """start() catches RuntimeError, logs the conflicting tool name, and does not set _started."""
    import logging

    from ddtrace.internal.datadog.profiling import native_call_monitor

    native_call_monitor._started = False

    sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "conflict")
    try:
        with caplog.at_level(logging.ERROR, logger="ddtrace.internal.datadog.profiling.native_call_monitor"):
            native_call_monitor.start()
        assert native_call_monitor._started is False
        assert "conflict" in caplog.text
        assert "sys.monitoring already claimed by another tool" in caplog.text
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
def test_start_native_monitoring_success() -> None:
    """start() should set _started when start_native_monitoring succeeds."""
    from ddtrace.internal.datadog.profiling import native_call_monitor

    native_call_monitor._started = False

    with mock.patch(
        "ddtrace.internal.datadog.profiling.stack._stack.start_native_monitoring",
    ):
        native_call_monitor.start()

    assert native_call_monitor._started is True
    native_call_monitor._started = False


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
def test_native_frames_detection(tmp_path: Path) -> None:
    """Test that the top-most native C frame is tracked and appears as the leaf frame in the profile."""
    test_name = "test_native_frames_detection"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sleep_loop() -> None:
        for _ in range(10):
            time.sleep(0.1)

    with stack.StackCollector():
        sleep_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_loop",
                    filename="test_stack_native.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess(err=None)
def test_native_frames_preserved_after_fork() -> None:
    """Test that native C frames are preserved in forked children.

    sys.monitoring returns DISABLE for each call site, so it only fires once.
    After fork, the registry must retain the parent's call site data, otherwise
    native frames are lost forever in the child with no way to re-populate them.

    This simulates a gunicorn-style fork: the profiler is running when fork()
    happens, the sampler restarts via the atfork handler, and the child profiles
    the same code paths that were already warmed up in the parent.
    """
    import _thread
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_preserved_after_fork"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def compress_loop() -> None:
        data = b"x" * 100000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(data)

    # Start the profiler and warm up call sites. sys.monitoring returns DISABLE
    # after the first fire, so the callback won't trigger again for these sites.
    # Fork while the profiler is still running (like gunicorn workers).
    collector = stack.StackCollector()
    collector.start()
    compress_loop()

    # Fork while profiler is running. The sampler restarts via the atfork
    # handler, and the NativeCallRegistry data is preserved.
    pid = os.fork()
    if pid == 0:
        # ----- child process -----
        # Reconfigure ddup to write to a child-specific file so we can
        # read the child's profile independently.
        child_pprof_prefix = str(tmp_path / (test_name + "_child"))
        child_output = child_pprof_prefix + "." + str(os.getpid())

        ddup.config(env="test", service=test_name, version="my_version", output_filename=child_pprof_prefix)
        ddup.start()
        ddup.upload()

        # The sampler restarted automatically via atfork. Run the same C
        # calls — the registry should still have the call site entries from
        # the parent, so native frames should appear.
        compress_loop()

        collector.stop()
        ddup.upload()

        profile = pprof_utils.parse_newest_profile(child_output)
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0

        try:
            pprof_utils.assert_profile_has_sample(
                profile,
                samples=samples,
                expected_sample=pprof_utils.StackEvent(
                    thread_id=_thread.get_ident(),
                    thread_name="MainThread",
                    locations=[loc("compress"), loc("compress_loop", FILE_NAME)],
                ),
                print_samples_on_failure=True,
            )
        except AssertionError:
            os._exit(1)
        os._exit(0)
    else:
        # ----- parent process -----
        collector.stop()
        _, status = os.waitpid(pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
        assert exit_code == 0, "Child process failed: native frames were not preserved after fork"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_hashlib() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sha256_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(b"Hello, world!")

    with stack.StackCollector():
        sha256_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("sha256_loop", FILE_NAME)],
        [loc("monotonic"), loc("sha256_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(),
                thread_name="MainThread",
                locations=exp_stack,
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_hashlib_kwarg() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sha256_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(b"Hello, world!")

    with stack.StackCollector():
        sha256_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("sha256_loop", FILE_NAME)],
        [loc("monotonic"), loc("sha256_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_native_frames_detection_numpy_flat() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    import numpy as np

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def numpy_loop() -> None:
        test_start = time.monotonic()
        while time.monotonic() - test_start < 2:
            mat = np.random.rand(1000, 1000)
            np.matmul(mat, mat)

    with stack.StackCollector():
        numpy_loop()

    ddup.upload()

    all_files_for_pid = sorted([x for x in os.listdir(tmp_path) if ".pprof" in x])
    parent_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(os.getpid())}." in f]
    profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    profile = pprof_utils.merge_profiles(profiles)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("matmul"), loc("numpy_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_native_frames_detection_numpy_nested() -> None:
    """Test that numpy.matmul appears as the top-most native C frame in the profile."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    import numpy as np

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def numpy_loop() -> None:
        test_start = time.monotonic()
        while time.monotonic() - test_start < 2:
            np.matmul(np.random.rand(1000, 1000), np.random.rand(1000, 1000))

    with stack.StackCollector():
        numpy_loop()

    ddup.upload()

    all_files_for_pid = sorted([x for x in os.listdir(tmp_path) if ".pprof" in x])
    parent_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(os.getpid())}." in f]
    profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    profile = pprof_utils.merge_profiles(profiles)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("matmul"), loc("numpy_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_zlib() -> None:
    """Test module-level C function: zlib.compress."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_zlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def zlib_loop() -> None:
        data = b"x" * 100000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(data)

    with stack.StackCollector():
        zlib_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("compress"), loc("zlib_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_sorted_builtin() -> None:
    """Test builtin function: sorted()."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_sorted_builtin"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sorted_loop() -> None:
        data = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            sorted(data)

    with stack.StackCollector():
        sorted_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sorted"), loc("sorted_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_list_sort_method() -> None:
    """Test method call: list.sort()."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_list_sort"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sort_loop() -> None:
        original = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            data = original.copy()
            data.sort()

    with stack.StackCollector():
        sort_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sort"), loc("sort_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_regex_method() -> None:
    """Test method call on C object: Pattern.findall()."""
    import _thread
    import os
    import pathlib
    import re
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_regex"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def regex_loop() -> None:
        pattern = re.compile(r"[a-z]+")
        text = "hello world foo bar baz " * 10000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            pattern.findall(text)

    with stack.StackCollector():
        regex_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("findall"), loc("regex_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_expression_arg() -> None:
    """Test C call with expression as argument: zlib.compress(chunk_a + chunk_b)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_expression_arg"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def compress_concat_loop() -> None:
        chunk_a = b"x" * 50000
        chunk_b = b"y" * 50000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(chunk_a + chunk_b)

    with stack.StackCollector():
        compress_concat_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("compress"), loc("compress_concat_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_nested_c_calls() -> None:
    """Test nested C calls: hashlib.sha256(zlib.compress(data))."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_nested_c_calls"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def nested_loop() -> None:
        data = os.urandom(100000)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(zlib.compress(data))

    with stack.StackCollector():
        nested_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("nested_loop", FILE_NAME)],
        [loc("compress"), loc("nested_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_call_kw() -> None:
    """Test builtin with keyword argument: sorted(data, reverse=True)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_call_kw"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sorted_kw_loop() -> None:
        data = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            sorted(data, reverse=True)

    with stack.StackCollector():
        sorted_kw_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sorted"), loc("sorted_kw_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_many_args() -> None:
    """Test C function with many positional arguments: hashlib.pbkdf2_hmac(algo, pwd, salt, iters)."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_many_args"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def pbkdf2_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.pbkdf2_hmac("sha256", b"password", b"salt", 50000)

    with stack.StackCollector():
        pbkdf2_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("pbkdf2_hmac"), loc("pbkdf2_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_method_two_args() -> None:
    """Test C method call with 2 arguments: Pattern.sub(repl, text)."""
    import _thread
    import os
    import pathlib
    import re
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_method_two_args"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def regex_sub_loop() -> None:
        pattern = re.compile(r"[a-z]+")
        text = "hello world foo bar baz " * 10000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            pattern.sub("X", text)

    with stack.StackCollector():
        regex_sub_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sub"), loc("regex_sub_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_method_on_literal() -> None:
    """Test method call on a literal: b''.join(chunks)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_method_on_literal"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def join_loop() -> None:
        chunks = [b"x" * 1000] * 1000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            b"".join(chunks)

    with stack.StackCollector():
        join_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("join"), loc("join_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_sequential_calls() -> None:
    """Test multiple sequential C calls in one function: compress then sha256."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name, filename="", line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_sequential_calls"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sequential_loop() -> None:
        data = os.urandom(100000)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(data)
            hashlib.sha256(data)

    with stack.StackCollector():
        sequential_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("compress"), loc("sequential_loop", FILE_NAME)],
        [loc("sha256"), loc("sequential_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_math_factorial() -> None:
    """Test module-level C function: math.factorial."""
    import _thread
    import math
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_math_factorial"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def factorial_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            math.factorial(10000)

    with stack.StackCollector():
        factorial_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("factorial"), loc("factorial_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_isinstance() -> None:
    """Test builtin function: isinstance()."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_isinstance"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def isinstance_loop() -> None:
        classes = (int, float, str, bytes, list, dict, tuple, set, frozenset, bool)
        obj = "hello"
        end = time.monotonic() + 2
        while time.monotonic() < end:
            for _ in range(10000):
                isinstance(obj, classes)

    with stack.StackCollector():
        isinstance_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("isinstance"), loc("isinstance_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_constructors() -> None:
    """Test builtin type constructors: dict(), list(), set()."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_constructors"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    source = [(i, i) for i in range(10000)]

    def dict_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            dict(source)

    def list_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            list(source)

    def set_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            set(source)

    with stack.StackCollector():
        dict_loop()
        list_loop()
        set_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("dict"), loc("dict_loop", FILE_NAME)],
        [loc("list"), loc("list_loop", FILE_NAME)],
        [loc("set"), loc("set_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_eval() -> None:
    """Test builtin function: eval()."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_eval"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    code = compile("sum(range(10000))", "<string>", "eval")

    def eval_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            eval(code)

    with stack.StackCollector():
        eval_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("eval"), loc("eval_loop", FILE_NAME)],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_native_frames_detection_str_repr() -> None:
    """Test dunder methods: str() and repr() on a large container."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_native_frames_detection_str_repr"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    big_list = list(range(100000))

    def str_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            str(big_list)

    def repr_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            repr(big_list)

    with stack.StackCollector():
        str_loop()
        repr_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("str"), loc("str_loop", FILE_NAME)],
        [loc("repr"), loc("repr_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess()
def test_top_c_frame_detection_nested_sort_with_key() -> None:
    """Test nested native frames: sort(key=py) -> sort(key=py) -> factorial."""
    import _thread
    import math
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = "test_stack_native.py"

    def loc(function_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_nested_sort_with_key"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def inner_key(x: int) -> int:
        return math.factorial(x + 2000)

    def outer_key(x: int) -> int:
        inner_data = list(range(x + 1))
        inner_data.sort(key=inner_key)
        return inner_data[-1] if inner_data else 0

    def outer_func() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            data = list(range(15, 0, -1))
            data.sort(key=outer_key)

    with stack.StackCollector():
        outer_func()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        # outer sort called from outer_func
        [loc("sort"), loc("outer_func", FILE_NAME)],
        # inner sort called from outer_key, which is the key for the outer sort
        [loc("sort"), loc("outer_key", FILE_NAME), loc("sort"), loc("outer_func", FILE_NAME)],
        # factorial called from inner_key, which is the key for the inner sort
        [
            loc("factorial"),
            loc("inner_key", FILE_NAME),
            loc("sort"),
            loc("outer_key", FILE_NAME),
            loc("sort"),
            loc("outer_func", FILE_NAME),
        ],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )
