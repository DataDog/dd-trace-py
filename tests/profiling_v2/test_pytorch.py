import os
import sys

import pytest

from tests.profiling.collector import pprof_utils
from tests.utils import call_program


@pytest.mark.skipif(not os.getenv("DD_PROFILING_PYTORCH_ENABLED", False), reason="Not testing pytorch GPU")
def test_call_script_pytorch_gpu(tmp_path, monkeypatch):
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    monkeypatch.setenv("DD_PROFILING_PYTORCH_ENABLED", "1")
    stdout, stderr, exitcode, pid = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_pytorch_gpu.py")
    )
    assert exitcode == 0, f"Profiler exited with code {exitcode}. Stderr: {stderr}"

    profile = pprof_utils.parse_profile(filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "gpu-time")
    assert len(samples) > 0
    print("number of gpu time samples: ", len(samples))
    print("first sample: ", samples[0])

    expected_sample = pprof_utils.StackEvent(
        locations=[
            pprof_utils.StackLocation(
                function_name="Memset (Device)",
                filename="unknown-file",
                line_no=0,
            ),
            pprof_utils.StackLocation(
                function_name="PYTORCH_DeviceType.CUDA",
                filename="unknown-file",
                line_no=0,
            ),
        ],
    )
    pprof_utils.assert_profile_has_sample(profile, samples=samples, expected_sample=expected_sample)

    gpu_device_label_samples = pprof_utils.get_samples_with_label_key(profile, "gpu device name")
    assert len(gpu_device_label_samples) > 0
