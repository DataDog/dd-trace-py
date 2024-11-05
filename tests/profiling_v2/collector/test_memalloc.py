import os

from ddtrace.profiling import Profiler
from ddtrace.settings.profiling import config
from tests.profiling.collector import pprof_utils


def _allocate_1k():
    return [object() for _ in range(1000)]


def test_heap_samples_collected(tmp_path, monkeypatch):
    # Test for https://github.com/DataDog/dd-trace-py/issues/11069
    test_name = "test_heap"
    pprof_prefix = str(tmp_path / test_name)
    monkeypatch.setattr(config, "output_pprof", pprof_prefix)
    monkeypatch.setattr(config.heap, "sample_size", 1024)
    output_filename = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    x = _allocate_1k()  # noqa: F841
    p.stop()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "heap-space")
    assert len(samples) > 0
