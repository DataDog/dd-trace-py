import os
import sys

import pytest

from tests.profiling.collector import pprof_utils
from tests.utils import call_program


try:
    import torch  # noqa: F401

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


@pytest.mark.skipif(not _HAS_TORCH, reason="torch is not installed")
def test_call_script_pytorch_cpu(tmp_path, monkeypatch):
    """The torch profiler integration should reconstruct the operator call tree.

    Each torch event's stack is built by walking the ``cpu_parent`` chain, so a
    nested operator (e.g. ``aten::addmm`` under ``aten::linear``) must show up as
    a multi-frame stack rooted at the ``PYTORCH_DeviceType.CPU`` pseudo-frame,
    rather than as a flat two-frame stack.
    """
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    monkeypatch.setenv("DD_PROFILING_PYTORCH_ENABLED", "1")
    _, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_pytorch_cpu.py")
    )
    assert exitcode == 0, f"Profiler exited with code {exitcode}. Stderr: {stderr}"

    profile = pprof_utils.parse_newest_profile(filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0, "Expected at least one cpu-time sample"

    # Find at least one sample with a reconstructed (nested) stack: more than the
    # old flat two frames ([op_name, PYTORCH_DeviceType.CPU]) and rooted at the
    # CPU device pseudo-frame.
    nested_found = False
    for sample in samples:
        locations = [pprof_utils.get_location_from_id(profile, loc_id) for loc_id in sample.location_id]
        if len(locations) < 3:
            continue
        if locations[-1].function_name != "PYTORCH_DeviceType.CPU":
            continue
        # The frames between the leaf and the device root are the operator
        # ancestor chain; require at least one genuine parent operator.
        op_frames = [loc.function_name for loc in locations[:-1]]
        if len(op_frames) >= 2:
            nested_found = True
            break

    assert nested_found, "Expected at least one cpu-time sample with a nested operator stack"


@pytest.mark.skipif(not os.getenv("DD_PROFILING_PYTORCH_ENABLED", False), reason="Not testing pytorch GPU")
def test_call_script_pytorch_gpu(tmp_path, monkeypatch):
    from ddtrace.profiling.collector.pytorch import _DEVICE_FRAME_FILE_NAME
    from ddtrace.profiling.collector.pytorch import _FILE_PLACEHOLDER

    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    monkeypatch.setenv("DD_PROFILING_PYTORCH_ENABLED", "1")
    _, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_pytorch_gpu.py")
    )
    assert exitcode == 0, f"Profiler exited with code {exitcode}. Stderr: {stderr}"

    profile = pprof_utils.parse_newest_profile(filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "gpu-time")
    assert len(samples) > 0
    print("number of gpu time samples: ", len(samples))
    print("first sample: ", samples[0])

    expected_sample = pprof_utils.StackEvent(
        locations=[
            pprof_utils.StackLocation(
                function_name="Memset (Device)",
                filename=_FILE_PLACEHOLDER,
                line_no=0,
            ),
            pprof_utils.StackLocation(
                function_name="PYTORCH_DeviceType.CUDA",
                filename=_DEVICE_FRAME_FILE_NAME,
                line_no=0,
            ),
        ],
    )
    pprof_utils.assert_profile_has_sample(profile, samples=samples, expected_sample=expected_sample)

    gpu_device_label_samples = pprof_utils.get_samples_with_label_key(profile, "gpu device name")
    assert len(gpu_device_label_samples) > 0
