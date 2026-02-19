import _thread
import os
from pathlib import Path
import time

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
import pprof_utils


FILE_NAME = os.path.basename(__file__)


def loc(function_name: str, filename: str = FILE_NAME, line_no: int = -1) -> pprof_utils.StackLocation:
    return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)


@pytest.mark.subprocess()
def test_top_c_frame_detection_hashlib(tmp_path: Path) -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import hashlib

    test_name = "test_top_c_frame_detection_hashlib"
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


@pytest.mark.subprocess()
def test_top_c_frame_detection_hashlib_kwarg(tmp_path: Path) -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import hashlib

    test_name = "test_top_c_frame_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sha256_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(data=b"Hello, world!")

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


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_top_c_frame_detection_numpy_flat(tmp_path: Path) -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import numpy as np

    test_name = "test_top_c_frame_detection_hashlib"
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

    expected_stacks = [
        [loc("matmul"), loc("numpy_loop", FILE_NAME)],
        [loc("rand"), loc("numpy_loop", FILE_NAME)],
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


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_top_c_frame_detection_numpy_nested(tmp_path: Path) -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import numpy as np

    test_name = "test_top_c_frame_detection_hashlib"
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
    print(f"Parent files: {parent_files}")
    profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    profile = pprof_utils.merge_profiles(profiles)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("matmul"), loc("numpy_loop", FILE_NAME)],
        [loc("rand"), loc("numpy_loop", FILE_NAME)],
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
